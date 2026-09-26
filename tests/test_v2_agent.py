from src.agent import MarketAgent
from src.config import Settings
from src.memory import new_session_state
from src.tools import classify_and_plan
from tests.sample_data import SAMPLE_EVENTS, SAMPLE_NEWS, sample_points


def settings_without_model() -> Settings:
    return Settings(enable_llm=False, hf_token="", openai_api_key="", llm_cooldown=1)


def test_planner_selects_relevant_tools_and_respects_step_limit():
    classification, plan, tools = classify_and_plan(
        "What are the latest Fed developments and this week's CPI calendar?", 3,
    )
    assert "central-bank" in classification or "calendar" in classification
    assert "market_snapshot" in tools
    assert len(tools) <= 3
    assert plan[-1].startswith("Build and audit")


def test_agent_builds_trace_and_session_memory_without_model():
    agent = MarketAgent(settings_without_model())
    answer, trace, state, memory = agent.research(
        "Prepare a morning brief with the main developments.",
        sample_points(), SAMPLE_NEWS, SAMPLE_EVENTS, new_session_state(),
    )
    assert "Audited sources" in answer
    assert "Research Trace" in trace
    assert "market_snapshot" in trace
    assert len(state["turns"]) == 1
    assert "morning brief" in memory


def test_session_memory_isolation():
    agent = MarketAgent(settings_without_model())
    first = new_session_state()
    second = new_session_state()
    _, _, first, _ = agent.research("What happened to rates?", sample_points(), SAMPLE_NEWS, SAMPLE_EVENTS, first)
    assert len(first["turns"]) == 1
    assert second["turns"] == []
