import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from backend import api, reports
from backend.prompts import REGULAMIND_SYSTEM_PROMPT


class RegulaMindPromptTests(unittest.TestCase):
    def test_chat_requests_apply_prompt_for_none_one_and_multiple_sources(self):
        source = {
            "id": "S1",
            "title": "Capital Requirements",
            "document": "rbi_direction.txt",
            "excerpt": "Maintain the required capital ratio.",
            "search_queries": ["capital requirement"],
        }
        cases = (
            ("NONE", [], "no_retrieval", False),
            ("ONE", [source], "single", True),
            ("MULTI", [source, {**source, "id": "S2"}], "single", True),
        )

        for label, sources, retrieval_mode, sufficient in cases:
            with self.subTest(strategy=label):
                client = Mock()
                response = Mock()
                response.choices = [Mock(message=Mock(content="Grounded answer."))]
                client.chat.completions.create.return_value = response
                with (
                    patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}),
                    patch("backend.api.Groq", return_value=client),
                ):
                    api._answer_with_llm(
                        "What is the requirement?",
                        sources,
                        sufficient,
                        [{"role": "user", "content": "Earlier question"}],
                        retrieval_mode,
                    )

                messages = client.chat.completions.create.call_args.kwargs["messages"]
                self.assertEqual(messages[0]["role"], "system")
                self.assertIn(REGULAMIND_SYSTEM_PROMPT, messages[0]["content"])
                self.assertIn("Earlier question", messages[1]["content"])
                self.assertEqual(messages[-1]["content"], "What is the requirement?")
                for item in sources:
                    self.assertIn(f"[{item['id']}]", messages[0]["content"])
                if label == "NONE":
                    self.assertIn("No RBI knowledge-base evidence", messages[0]["content"])

    def test_chat_provides_extracted_policy_text_without_auditing_it(self):
        private_policy_text = "INTERNAL POLICY TEXT - do not expose"
        source = {
            "id": "S1",
            "title": "Capital Requirements",
            "document": "rbi_direction.txt",
            "excerpt": "Maintain the required capital ratio.",
            "relevance": 0.9,
        }

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, query, parameters):
                if (
                    "SELECT text_content FROM policies" in query
                    and parameters == ("policy-id", "user-id")
                ):
                    return FakeRows({"text_content": private_policy_text})
                return FakeRows()

        class FakeRows:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

            def fetchall(self):
                return []

        class FakeRetriever:
            status = {"collection": "test-collection"}

            def __init__(self):
                self.queries = []

            def retrieve_planned_query(self, query):
                self.queries.append(query)
                return {"sufficient": True, "sources": [source]}

        fake_retriever = FakeRetriever()
        audit_events = []

        async def run_in_thread(function, *args):
            return function(*args)

        with (
            patch("backend.api.connect", return_value=FakeConnection()),
            patch("backend.api.add_message"),
            patch("backend.api.retriever", fake_retriever),
            patch("backend.api.record_audit_event", side_effect=lambda *a, **k: audit_events.append((a, k))),
            patch("backend.api.asyncio.to_thread", side_effect=run_in_thread),
        ):
            history, retrieved = asyncio.run(
                api._retrieve_chat_context(
                    api.ChatRequest(
                        message="Compare this policy with RBI requirements.",
                        policy_id="policy-id",
                    ),
                    "conversation-id",
                    "Compare this policy with RBI requirements.",
                    "request-id",
                    "user-id",
                )
            )

        self.assertEqual(history, [])
        self.assertEqual(retrieved["policy_text"], private_policy_text)
        self.assertIn(private_policy_text, fake_retriever.queries[0])
        self.assertNotIn(private_policy_text, repr(audit_events))

        client = Mock()
        response = Mock()
        response.choices = [Mock(message=Mock(content="Potential gap [S1]."))]
        client.chat.completions.create.return_value = response
        with (
            patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}),
            patch("backend.api.Groq", return_value=client),
        ):
            api._answer_with_llm(
                "Compare this policy with RBI requirements.",
                retrieved["sources"],
                retrieved["sufficient"],
                history,
                retrieved["retrieval_mode"],
                retrieved["policy_text"],
            )

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn(private_policy_text, messages[-1]["content"])

    def test_report_analysis_supplies_prompt_extracted_document_and_rbi_evidence(self):
        policy_text = "The bank must maintain a capital ratio of 12 percent."
        source = {
            "id": "S1",
            "document": "rbi_direction.txt",
            "title": "Capital Requirements",
            "page": "4",
            "excerpt": "Maintain the required capital ratio.",
        }
        response = Mock()
        response.choices = [Mock(message=Mock(content=json.dumps({
            "executive_summary": "Review required.",
            "requirements": [],
            "comparison": [],
            "findings": [],
            "recommendations": [],
            "limitations": [],
        })))]
        client = Mock()
        client.chat.completions.create.return_value = response

        with patch("backend.reports._groq_client", return_value=client):
            reports.analyze_evidence(
                report_type="policy_compliance",
                query="Assess the policy.",
                sources=[source],
                policy_text=policy_text,
            )

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], REGULAMIND_SYSTEM_PROMPT)
        self.assertIn(policy_text, messages[1]["content"])
        self.assertIn(source["excerpt"], messages[1]["content"])
        self.assertIn("[S1]", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
