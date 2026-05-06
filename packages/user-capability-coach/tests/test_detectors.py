"""Tests for detectors.py — heuristic signal detection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.detectors import detect, DetectionOutput
from tools.taxonomy import IssueType, Domain


def _issue_types(result: DetectionOutput) -> list[str]:
    return [c.issue_type.value for c in result.candidates]


# ── Good prompts (must NOT trigger v1 issues) ──────────────────────────────

class TestGoodPrompts:
    def test_clear_translation(self):
        r = detect("请把这段文字翻译成英文：今天天气很好")
        assert IssueType.MISSING_OUTPUT_CONTRACT not in [c.issue_type for c in r.candidates]

    def test_sql_with_constraints(self):
        r = detect("给我一个查询，从 users 表查过去30天的用户，按注册时间降序")
        assert IssueType.MISSING_GOAL not in [c.issue_type for c in r.candidates]

    def test_json_to_csv(self):
        r = detect('把这个JSON转成CSV：{"name": "Alice"}')
        assert not r.candidates

    def test_markdown_table_specified(self):
        r = detect("帮我对比 React 和 Vue，给 markdown 表格")
        assert IssueType.MISSING_GOAL not in [c.issue_type for c in r.candidates]

    def test_greeting_not_flagged(self):
        r = detect("hi")
        assert not r.candidates

    def test_thanks_not_flagged(self):
        r = detect("thanks!")
        assert not r.candidates

    def test_explicit_format_en(self):
        r = detect("List 5 mountains in Asia in a table with heights.")
        assert not r.candidates or all(
            c.issue_type != IssueType.MISSING_OUTPUT_CONTRACT for c in r.candidates
        )

    def test_python_function_with_spec(self):
        r = detect("Write a Python function that checks if a number is prime. Return True or False.")
        assert IssueType.MISSING_GOAL not in [c.issue_type for c in r.candidates]

    def test_question_mark_implies_goal(self):
        r = detect("What is the difference between TCP and UDP?")
        assert IssueType.MISSING_GOAL not in [c.issue_type for c in r.candidates]

    def test_short_developer_operations_are_clear(self):
        for text in [
            "提交并推送git吧",
            "跑一下测试",
            "发个 PR",
            "commit and push",
            "run the tests",
        ]:
            r = detect(text)
            assert not r.candidates, f"{text!r} should be treated as an executable command"


# ── Weak prompts (must trigger specific issues) ─────────────────────────────

class TestWeakPrompts:
    def test_interface_doc_no_format(self):
        r = detect("帮我写一个接口文档")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_OUTPUT_CONTRACT in types

    def test_generate_doc_no_format(self):
        r = detect("请帮我生成文档")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_OUTPUT_CONTRACT in types

    def test_write_report_no_format(self):
        r = detect("Write me a report.")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_OUTPUT_CONTRACT in types

    def test_multi_phase_zh(self):
        r = detect("分析这个系统然后给重构方案、代码、测试、上线步骤")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.OVERLOADED_REQUEST in types

    def test_multi_phase_en_verbs(self):
        r = detect("Analyze the system, design the solution, implement it, write tests, and deploy")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.OVERLOADED_REQUEST in types

    def test_multi_phase_en_nouns(self):
        r = detect("Write the spec, code, tests, and deployment guide")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.OVERLOADED_REQUEST in types

    def test_missing_goal_look_at_this(self):
        r = detect("看看这个")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_GOAL in types

    def test_missing_goal_optimize_system(self):
        r = detect("帮我优化这个系统")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_GOAL in types

    def test_missing_goal_help_bare(self):
        r = detect("help")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_GOAL in types

    def test_missing_goal_make_better(self):
        r = detect("Make it better.")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_GOAL in types

    def test_vague_start_with_data(self):
        """'Do something with this data: ...' is missing_goal even with content."""
        r = detect("Do something with this data:\n1, 2, 3, 4, 5")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_GOAL in types

    def test_short_output_verb_triggers_missing_contract(self):
        """Short 2-word 'Generate output.' should still flag missing_output_contract."""
        r = detect("Generate output.")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_OUTPUT_CONTRACT in types

    def test_chinese_bare_code_flags_missing_goal(self):
        """'请写代码' — labeler convention: missing_goal (what code?)."""
        r = detect("请写代码")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_GOAL in types

    def test_english_write_something_flags_missing_contract(self):
        """'Write something.' — action stated, format missing → missing_output_contract."""
        r = detect("Write something.")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_OUTPUT_CONTRACT in types


# ── Sensitive prompts (must suppress all issues) ───────────────────────────

class TestSensitivePrompts:
    def test_mental_health_zh(self):
        r = detect("我最近一直在想一些不好的念头")
        assert r.is_sensitive
        assert not r.candidates

    def test_health_en(self):
        r = detect("My doctor said I might have cancer")
        assert r.is_sensitive
        assert not r.candidates

    def test_legal(self):
        r = detect("I'm being sued. What are my options?")
        assert r.is_sensitive

    def test_bereavement_zh(self):
        r = detect("我父亲刚刚去世，我需要处理遗产问题")
        assert r.is_sensitive

    def test_financial_distress_zh(self):
        r = detect("我最近财务压力很大，有点喘不过气来")
        assert r.is_sensitive

    def test_emergency(self):
        r = detect("There's been an accident at our facility.")
        # May or may not be sensitive — but should not produce coaching candidates
        # "accident" is in sensitive keywords
        assert r.is_sensitive or not r.candidates


# ── Confidence and fixability invariants ───────────────────────────────────

class TestDetectorInvariants:
    def test_confidence_range(self):
        for text in ["帮我写一个接口文档", "Make it better.", "分析+设计+开发+测试+部署"]:
            r = detect(text)
            for c in r.candidates:
                assert 0.0 <= c.confidence <= 1.0
                assert 0.0 <= c.severity <= 1.0
                assert 0.0 <= c.fixability <= 1.0

    def test_sensitive_no_candidates(self):
        r = detect("我父亲去世了")
        if r.is_sensitive:
            assert not r.candidates

    def test_engineering_urgency_is_not_sensitive(self):
        """Regression: '紧急' / 'urgent' / 'emergency' alone are engineering
        urgency, not personal sensitivity. They MUST NOT force domain=SENSITIVE
        (which would suppress coaching for ordinary on-call work)."""
        from tools.taxonomy import Domain
        cases = [
            ("紧急！帮我看看这个线上bug", Domain.CODING),
            ("紧急 bug", Domain.CODING),
            ("urgent: fix this deploy", Domain.CODING),
            ("emergency: production database down", Domain.CODING),
        ]
        for text, expected_domain in cases:
            r = detect(text)
            assert r.domain == expected_domain, (
                f"{text!r} got domain={r.domain.value}, expected {expected_domain.value}"
            )
            assert r.is_sensitive is False, f"{text!r} was flagged sensitive"
            # Urgency flag should fire for the urgency keywords
            assert r.is_urgent is True, f"{text!r} should be urgent"

    def test_true_sensitive_still_caught(self):
        """Ensure the fix for engineering urgency didn't remove real sensitivity."""
        from tools.taxonomy import Domain
        cases = [
            "我最近感觉很抑郁",
            "救命啊，活不下去了",
            "紧急事故",          # 事故 remains a sensitive keyword
            "紧急手术",          # 手术 is a health keyword
        ]
        for text in cases:
            r = detect(text)
            assert r.domain == Domain.SENSITIVE, (
                f"{text!r} got domain={r.domain.value}, expected SENSITIVE"
            )
            assert r.is_sensitive is True, f"{text!r} lost sensitive flag"

    def test_technical_crash_is_not_sensitive(self):
        """Regression: bare '崩溃了' is commonly tech-speak ('系统崩溃了',
        '服务崩溃了'). Only emotional breakdown patterns ('情绪崩溃',
        '心情很差', 'mental breakdown') should be sensitive."""
        from tools.taxonomy import Domain
        # Technical — must NOT be sensitive
        for text in ["系统崩溃了", "服务崩溃了", "生产环境崩溃了"]:
            r = detect(text)
            assert not r.is_sensitive, f"{text!r} wrongly flagged sensitive"
            assert r.domain != Domain.SENSITIVE, f"{text!r} wrongly domain=sensitive"
        # Emotional — must STILL be sensitive
        for text in ["情绪崩溃了", "mental breakdown", "心情很差"]:
            r = detect(text)
            assert r.is_sensitive, f"{text!r} lost sensitive flag"
            assert r.domain == Domain.SENSITIVE, f"{text!r} lost sensitive domain"

    def test_social_phrase_no_candidates(self):
        for phrase in ["hi", "hello", "thanks", "好的", "明白", "谢谢"]:
            r = detect(phrase)
            assert not r.candidates, f"Should not flag '{phrase}'"

    def test_build_detection_from_agent_basic(self):
        from tools.detectors import build_detection_from_agent
        out = build_detection_from_agent(
            {
                "issue_type": "missing_goal",
                "confidence": 0.9,
                "severity": 0.8,
                "domain": "coding",
                "evidence_summary": "cross-turn ambiguity",
            },
            "some text",
        )
        assert out is not None
        assert len(out.candidates) == 1
        assert out.candidates[0].issue_type == IssueType.MISSING_GOAL
        assert out.candidates[0].confidence == 0.9
        assert out.domain.value == "coding"

    def test_build_detection_from_agent_null_issue(self):
        """Agent saying null = no candidate (suppresses rule false-positive)."""
        from tools.detectors import build_detection_from_agent
        out = build_detection_from_agent(
            {"issue_type": None, "domain": "coding"},
            "帮我写一个接口文档",  # rule would flag this
        )
        assert out is not None
        assert out.candidates == []

    def test_build_detection_from_agent_bad_enum_returns_none(self):
        from tools.detectors import build_detection_from_agent
        out = build_detection_from_agent(
            {"issue_type": "bogus_type"}, "any text"
        )
        assert out is None

    def test_build_detection_domain_alias_docs_maps_to_writing(self):
        """Common agent vocabulary 'docs' should resolve to writing, not
        reject the whole classification."""
        from tools.detectors import build_detection_from_agent
        out = build_detection_from_agent(
            {"issue_type": "missing_output_contract",
             "confidence": 0.8, "severity": 0.75,
             "domain": "docs"},
            "help me write API docs",
        )
        assert out is not None
        assert out.domain.value == "writing"

    def test_build_detection_unknown_domain_falls_back_to_other(self):
        """Totally unknown domain → OTHER, don't reject."""
        from tools.detectors import build_detection_from_agent
        out = build_detection_from_agent(
            {"issue_type": "missing_goal",
             "confidence": 0.8, "severity": 0.75,
             "domain": "photography"},
            "critique my photos",
        )
        assert out is not None
        assert out.domain.value == "other"

    def test_build_detection_bad_cost_signal_falls_back(self):
        """Unknown cost_signal → NONE, don't reject."""
        from tools.detectors import build_detection_from_agent
        out = build_detection_from_agent(
            {"issue_type": "missing_goal",
             "confidence": 0.8, "severity": 0.75,
             "cost_signal": "bogus_cost"},
            "text",
        )
        assert out is not None
        # Candidate still present
        assert len(out.candidates) == 1
        assert out.candidates[0].cost_signal.value == "none"

    def test_empty_input_no_candidates(self):
        for phrase in ["", "   ", "\n\n\t"]:
            r = detect(phrase)
            assert not r.candidates, f"Empty input should not flag: {phrase!r}"
            assert r.domain == IssueType.MISSING_GOAL.value.split("_")[0] or r.domain.value == "other"

    def test_chinese_transformation_recognized(self):
        """'请改写这段：你好世界' — '改写' is a transformation verb even
        mid-sentence; the \\b word-boundary must NOT prevent matching
        between Chinese chars."""
        r = detect("请改写这段：你好世界")
        assert not r.candidates, (
            f"Chinese transformation prompt should have implicit output contract, "
            f"got {[c.issue_type.value for c in r.candidates]}"
        )

    def test_unbound_reference_at_start(self):
        """'It's broken.' / 'This doesn't work.' fires unbound_reference."""
        for text in ["It's broken.", "这个不对。", "that doesn't work"]:
            r = detect(text)
            types = [c.issue_type for c in r.candidates]
            assert IssueType.UNBOUND_REFERENCE in types, (
                f"{text!r}: expected unbound_reference in {types}"
            )

    def test_unbound_reference_not_triggered_on_benign_pronoun_start(self):
        """Regression: 'This is great!' / 'It is raining, what do I wear?'
        / 'That was delicious.' etc. start with bare pronouns but are
        not unbound references — require a problem-stating word too."""
        for text in [
            "This is great!",
            "It is raining, what do I wear?",
            "That was delicious.",
            "These are my notes",
            "This is working correctly",
        ]:
            r = detect(text)
            types = [c.issue_type for c in r.candidates]
            assert IssueType.UNBOUND_REFERENCE not in types, (
                f"{text!r}: should NOT flag unbound_reference, got {types}"
            )

    def test_missing_context_with_reference_no_content(self):
        """'Fix this bug' with no code → missing_context."""
        r = detect("Fix this bug for me please")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_CONTEXT in types

    def test_missing_context_with_code_block_suppressed(self):
        """'Fix this code' WITH a code block attached → no missing_context."""
        r = detect("Fix this code:\n```python\ndef foo():\n    pass\n```")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_CONTEXT not in types

    def test_missing_context_with_path_reference_suppressed(self):
        """Prompt references 'this bug' but gives a path — agent can
        read the file, so referent is resolvable. Shouldn't fire."""
        for text in [
            "Fix this bug on line 42 in app.py",
            "Review the following code at src/foo.py:15",
            "Look at @src/api.py",
            "This function in utils.py is slow",
        ]:
            r = detect(text)
            types = [c.issue_type for c in r.candidates]
            assert IssueType.MISSING_CONTEXT not in types, (
                f"{text!r}: path ref should bypass missing_context, got {types}"
            )

    def test_missing_context_with_error_message_suppressed(self):
        """'Debug this error: TypeError at line 42 in app.py' → no
        missing_context (the error message IS the content)."""
        r = detect(
            "Debug this error: TypeError: 'NoneType' object is not "
            "subscriptable at line 42 in app.py"
        )
        types = [c.issue_type for c in r.candidates]
        assert IssueType.MISSING_CONTEXT not in types

    def test_chinese_translation_target_language_recognized(self):
        """Target language mid-sentence must match: '翻译成英文的句子'."""
        r = detect("请帮我翻译成英文的句子")
        assert not r.candidates, (
            f"Chinese translation target should imply output contract, "
            f"got {[c.issue_type.value for c in r.candidates]}"
        )

    def test_give_me_the_x_is_missing_goal(self):
        """'Give me the answer.' — intent verb present but referent 'the answer'
        is unbound → missing_goal (not missing_output_contract)."""
        for text in [
            "Give me the answer.",
            "Show me the result.",
            "Tell me the solution.",
        ]:
            r = detect(text)
            types = [c.issue_type.value for c in r.candidates]
            assert IssueType.MISSING_GOAL.value in types, (
                f"{text!r}: expected missing_goal in {types}"
            )

    def test_missing_success_criteria_fires_without_metric(self):
        """Improve verb without verifiable success metric fires
        missing_success_criteria (may be outscored by other candidates
        in policy, but the detector produces it)."""
        for text in ["Optimize this function", "Improve performance", "优化这段代码"]:
            r = detect(text)
            types = [c.issue_type for c in r.candidates]
            assert IssueType.MISSING_SUCCESS_CRITERIA in types, (
                f"{text!r}: expected missing_success_criteria in {types}"
            )

    def test_missing_success_criteria_suppressed_with_metric(self):
        """Metric present → detector does NOT emit missing_success_criteria."""
        for text in [
            "Optimize this function to run under 10ms",
            "Improve performance until all tests pass",
            "优化这段代码，让它比现在快 3 倍",
            "Speed up this loop by 5x",
        ]:
            r = detect(text)
            types = [c.issue_type for c in r.candidates]
            assert IssueType.MISSING_SUCCESS_CRITERIA not in types, (
                f"{text!r}: metric should bypass, got {types}"
            )

    def test_conflicting_instructions_fires_on_explicit_markers(self):
        """Explicit shorter-vs-detailed conflict fires rule."""
        r = detect("Make it shorter but include all details")
        types = [c.issue_type for c in r.candidates]
        assert IssueType.CONFLICTING_INSTRUCTIONS in types

    def test_conflicting_instructions_noise_suppressed(self):
        """Generic 'X and also Y' without antagonist pair shouldn't fire."""
        for text in [
            "Write a summary and also a brief outline",
            "Review this code and also suggest improvements",
        ]:
            r = detect(text)
            types = [c.issue_type for c in r.candidates]
            assert IssueType.CONFLICTING_INSTRUCTIONS not in types

    def test_urgency_chinese_mid_sentence(self):
        """Urgency signal '马上'/'紧急' must match even when surrounded by
        other Chinese chars (\\b boundary doesn't apply between CJK chars)."""
        for urgent_text in ["这个很紧急", "请马上处理", "现在就要"]:
            r = detect(urgent_text)
            assert r.is_urgent, f"{urgent_text!r} should be flagged urgent"
