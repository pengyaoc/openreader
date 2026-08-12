"""Truth table for the regex rule engine. Pure functions, no I/O."""
from app.config import Rule, compile_rule
from app.ingest.rules import RawArticle, evaluate_rules


def art(**kw):
    defaults = dict(title="", summary="", content="", author="", url="")
    defaults.update(kw)
    return RawArticle(**defaults)


def test_no_rules_means_everything_passes():
    passed, matched = evaluate_rules(art(title="anything"), [])
    assert passed is True
    assert matched is None


def test_exclude_rule_drops_matching_article():
    rules = [compile_rule(Rule(action="exclude", field="title", pattern="(?i)sponsored"))]
    passed, matched = evaluate_rules(art(title="This is a Sponsored post"), rules)
    assert passed is False
    assert matched is not None
    assert matched.action == "exclude"


def test_exclude_rule_does_not_drop_non_matching_article():
    rules = [compile_rule(Rule(action="exclude", field="title", pattern="(?i)sponsored"))]
    passed, matched = evaluate_rules(art(title="Regular post"), rules)
    assert passed is True
    assert matched is None


def test_include_rule_requires_a_match_to_pass():
    rules = [compile_rule(Rule(action="include", field="title", pattern="(?i)llm"))]
    passed, matched = evaluate_rules(art(title="A post about databases"), rules)
    assert passed is False


def test_include_rule_passes_when_matched():
    rules = [compile_rule(Rule(action="include", field="title", pattern="(?i)llm"))]
    passed, matched = evaluate_rules(art(title="New LLM agent framework"), rules)
    assert passed is True
    assert matched.action == "include"


def test_source_with_no_include_rules_passes_everything_not_excluded():
    rules = [compile_rule(Rule(action="exclude", field="title", pattern="(?i)spam"))]
    passed, _ = evaluate_rules(art(title="Totally normal post"), rules)
    assert passed is True


def test_exclude_wins_over_include_when_both_match():
    rules = [
        compile_rule(Rule(action="include", field="title", pattern="(?i)llm")),
        compile_rule(Rule(action="exclude", field="title", pattern="(?i)sponsored")),
    ]
    passed, matched = evaluate_rules(art(title="Sponsored: new LLM tool"), rules)
    assert passed is False
    assert matched.action == "exclude"


def test_field_any_checks_all_fields_with_or():
    rules = [compile_rule(Rule(action="include", field="any", pattern="(?i)agent"))]
    passed, _ = evaluate_rules(
        art(title="Database news", summary="", content="Mentions agent workflows"),
        rules,
    )
    assert passed is True


def test_field_url_is_checked_independently():
    rules = [compile_rule(Rule(action="exclude", field="url", pattern="prnewswire"))]
    passed, _ = evaluate_rules(
        art(title="Announcement", url="https://prnewswire.com/x"), rules
    )
    assert passed is False


def test_rules_evaluated_in_order_first_exclude_short_circuits():
    # Two excludes; only the first should be reported as the matched rule.
    rules = [
        compile_rule(Rule(action="exclude", field="title", pattern="(?i)foo")),
        compile_rule(Rule(action="exclude", field="title", pattern="(?i)post")),
    ]
    passed, matched = evaluate_rules(art(title="Foo post"), rules)
    assert passed is False
    assert matched.pattern == "(?i)foo"
