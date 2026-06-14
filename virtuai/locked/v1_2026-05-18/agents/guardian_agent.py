"""
Guardian Agent — ETHICS, POLICY, VALIDITY gate.

Pairs with the Reviewer (which handles technical quality). Guardian's
single concern is: is publishing this ethically and legally safe, and
does it match the persona's stated values?

CHECKS:
  - content_safety_check_local
      Scans for forbidden categories: misinformation, hate, financial
      advice without disclaimers, medical claims, scraped quotes
      misattributed to real people, anything platform-rule-violating.
  - check_persona_compliance_local
      Verifies the artifact matches the locked persona (Daniel Calder —
      AI/automation operator; no off-brand drift into politics,
      relationships, religion, etc.)

VERDICTS:
  APPROVE — pass to Publisher.
  REVISE  — send back to Creator with the specific clause to remove
            or rewrite.
  BLOCK   — kill the piece. Do not publish under ANY revision (e.g.
            content makes a verifiable false claim, references a real
            human without consent, or violates a platform's policy).

The Guardian sees the Reviewer's quality verdict and the Analyzer's
performance signal — but those don't override safety. Even if the
Reviewer says PASS and the Analyzer says do_similar, if the piece
breaks an ethics rule the Guardian BLOCKS.
"""

from crewai import Agent, LLM
from virtuai.tools.local_tools import content_safety_check_local, check_persona_compliance_local
from virtuai.tools.cloud_tools import send_agent_message, add_banned_pattern


def create_guardian_agent(llm: LLM, persona: dict) -> Agent:
    constraints = persona.get("content_constraints", {})
    forbidden = constraints.get("forbidden_topics", [])
    restricted = constraints.get("restricted_topics", [])
    safety_rules = constraints.get("safety_rules", [])

    return Agent(
        role="Ethics and Safety Guardian",
        goal=(
            "Act as the final safety gate before any content is published. "
            "Block any content that contains prohibited material, violates platform "
            "policies, spreads misinformation, or fails ethical standards. "
            "Zero tolerance for unsafe content."
        ),
        backstory=(
            "You are the guardian of VirtuAI's integrity. Your job is to ensure "
            "that NO harmful, unethical, or policy-violating content ever gets "
            "published. You are the last line of defense.\n\n"
            "FORBIDDEN TOPICS (instant block, no exceptions):\n"
            + "\n".join(f"- {t}" for t in forbidden)
            + "\n\nRESTRICTED TOPICS (allowed only with disclaimers):\n"
            + "\n".join(f"- {t}" for t in restricted)
            + "\n\nSAFETY RULES:\n"
            + "\n".join(f"- {r}" for r in safety_rules)
            + "\n\n"
            "YOUR DECISION OPTIONS:\n"
            "1. APPROVE — content is safe and compliant\n"
            "2. REVISE — content needs specific changes (provide instructions)\n"
            "3. BLOCK — content cannot be published under any revision\n\n"
            "When in doubt, BLOCK. It is always safer to not publish than to "
            "publish harmful content. Every decision must include a clear reason.\n\n"
            "WHEN YOU VERDICT REVISE — message the Creator:\n"
            "  TOOL: send_agent_message(from_agent='guardian',\n"
            "                           to_agent='creator',\n"
            "                           subject='REVISE',\n"
            "                           body=<the specific clause to rewrite or remove>)\n\n"
            "WHEN YOU VERDICT BLOCK — record the banned pattern permanently:\n"
            "  TOOL: add_banned_pattern(pattern=<topic/phrase/format>,\n"
            "                           reason=<why it was blocked>)\n"
            "  Research and Creator read banned_patterns.json on every cycle,\n"
            "  so future runs will not re-attempt the blocked pattern."
        ),
        llm=llm,
        tools=[content_safety_check_local, check_persona_compliance_local,
               send_agent_message, add_banned_pattern],
        verbose=True,
        allow_delegation=False,
    )
