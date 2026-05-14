"""
MediGuard AI — LangChain Multi-Agent System

Creates 4 specialist agents (Cardiac, Respiratory, Thermal, Trend) that
monitor real-time vitals via Redis Pub/Sub concurrently.

Usage:
    # Requires OPENAI_API_KEY in .env
    python -m scripts.run_agents
"""

import asyncio
import json
import logging
import os
from collections import defaultdict, deque
from typing import Literal

import redis.asyncio as aioredis
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# Load environment variables
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mediguard.agents")


# ─── Data Models ────────────────────────────────────────────────────────────

class AgentOutput(BaseModel):
    """Structured JSON output format for all specialist agents."""
    agent: str = Field(description="Name of the agent (e.g., CardiacAgent, RespiratoryAgent)")
    patient_id: str = Field(description="UUID of the patient")
    severity: Literal["none", "low", "medium", "high", "critical"] = Field(
        description="Severity of the finding. Use 'none' if readings are perfectly normal."
    )
    reason: str = Field(description="Clinical reason for the severity level. Explain what is abnormal, or 'Normal vitals' if none.")
    recommended_action: str = Field(description="Recommended medical action. E.g., 'Administer oxygen', or 'Continue monitoring' if none.")


# ─── Agent System ───────────────────────────────────────────────────────────

class VitalsMultiAgentSystem:
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        
        # Initialize the LLM (Requires OPENAI_API_KEY)
        # Using a fast/cheap model since it processes high-frequency data
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        
        # History buffer for TrendAgent (max 10 readings per patient)
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))

        # Build agents
        self.cardiac_agent = self._create_agent(
            "CardiacAgent",
            "You monitor heart rate and blood pressure.\n"
            "Flags:\n"
            "- Tachycardia: heart rate > 100 bpm\n"
            "- Bradycardia: heart rate < 50 bpm\n"
            "- Hypertensive crisis: systolic BP > 180 mmHg\n"
            "- Hypotension: systolic BP < 90 mmHg"
        )
        
        self.respiratory_agent = self._create_agent(
            "RespiratoryAgent",
            "You monitor SpO2 and respiratory rate.\n"
            "Flags:\n"
            "- Hypoxia: SpO2 < 92%\n"
            "- Rapid breathing (Tachypnea): respiratory rate > 25 breaths/min"
        )
        
        self.thermal_agent = self._create_agent(
            "ThermalAgent",
            "You monitor body temperature.\n"
            "Flags:\n"
            "- Fever: temperature > 38.5°C\n"
            "- Hypothermia: temperature < 35.0°C"
        )
        
        self.trend_agent = self._create_trend_agent()

    def _create_agent(self, agent_name: str, rules: str):
        """Helper to create a single-reading specialist agent."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"You are the {agent_name}.\n{rules}\n\n"
                       "If all readings are within normal ranges, set severity to 'none', reason to 'Normal', and recommended_action to 'Continue monitoring'."),
            ("human", "Patient ID: {patient_id}\nVitals: {vitals_json}")
        ])
        return prompt | self.llm.with_structured_output(AgentOutput)

    def _create_trend_agent(self):
        """Creates the agent responsible for analyzing historical trends."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are the TrendAgent.\n"
                       "You analyze the last 10 vital readings (ordered oldest to newest) to detect deterioration trends.\n"
                       "Look for:\n"
                       "- Steadily dropping SpO2 (even if currently > 92%)\n"
                       "- Steadily rising or dropping heart rate or blood pressure\n"
                       "- Increasing respiratory rate\n\n"
                       "If the trend is stable or improving, set severity to 'none', reason to 'Stable trend', and recommended_action to 'Continue monitoring'. "
                       "Only flag actual concerning trends that indicate the patient is worsening over time."),
            ("human", "Patient ID: {patient_id}\nRecent History: {history_json}")
        ])
        return prompt | self.llm.with_structured_output(AgentOutput)

    # ─── Execution ──────────────────────────────────────────────────────────

    async def _invoke_agent(self, agent_name: str, agent, inputs: dict):
        """Invokes a specific agent and logs its output if actionable."""
        try:
            res: AgentOutput = await agent.ainvoke(inputs)
            
            # Print JSON if there is an alert
            if res.severity != "none":
                # Color code based on severity
                color = "\033[91m" if res.severity in ["high", "critical"] else "\033[93m"
                reset = "\033[0m"
                
                logger.warning(
                    f"{color}[{res.agent}] Patient {res.patient_id[:8]} - "
                    f"SEVERITY: {res.severity.upper()} | "
                    f"REASON: {res.reason} -> ACTION: {res.recommended_action}{reset}"
                )
                
                # Output the raw JSON string as requested
                print(json.dumps(res.model_dump(), indent=2))
                
        except Exception as e:
            logger.error(f"[{agent_name}] Failed to process: {e}")

    async def process_message(self, patient_id: str, vitals: dict):
        """Processes a single vital reading concurrently across all agents."""
        
        # 1. Update history for TrendAgent
        self.history[patient_id].append(vitals)
        
        # 2. Prepare inputs
        single_input = {"patient_id": patient_id, "vitals_json": json.dumps(vitals)}
        trend_input = {"patient_id": patient_id, "history_json": json.dumps(list(self.history[patient_id]))}

        # 3. Build concurrent tasks
        tasks = [
            self._invoke_agent("CardiacAgent", self.cardiac_agent, single_input),
            self._invoke_agent("RespiratoryAgent", self.respiratory_agent, single_input),
            self._invoke_agent("ThermalAgent", self.thermal_agent, single_input),
        ]
        
        # Only run TrendAgent if we have a reasonable baseline (e.g., at least 3 readings)
        if len(self.history[patient_id]) >= 3:
            tasks.append(self._invoke_agent("TrendAgent", self.trend_agent, trend_input))
            
        # 4. Execute all agents in parallel
        await asyncio.gather(*tasks)

    async def run(self):
        if not os.getenv("OPENAI_API_KEY"):
            logger.error("❌ OPENAI_API_KEY is not set in .env! LangChain agents require an API key.")
            return

        redis = aioredis.from_url(self.redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        
        # Subscribe to the pattern matching all patient vital channels
        await pubsub.psubscribe("vitals:*")
        
        logger.info("🤖 LangChain Multi-Agent System initialized.")
        logger.info("📡 Subscribed to Redis Pub/Sub 'vitals:*'. Waiting for data...")

        try:
            async for message in pubsub.listen():
                if message["type"] == "pmessage":
                    channel = message["channel"]
                    patient_id = channel.split(":")[-1]
                    data = json.loads(message["data"])
                    
                    logger.debug(f"Received vitals for {patient_id}. Dispatching to agents...")
                    
                    # Fire and forget processing to keep subscribing fast
                    asyncio.create_task(self.process_message(patient_id, data))
                    
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.punsubscribe("vitals:*")
            await redis.aclose()


if __name__ == "__main__":
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    system = VitalsMultiAgentSystem(redis_url)
    
    try:
        asyncio.run(system.run())
    except KeyboardInterrupt:
        logger.info("Multi-Agent System stopped by user.")
