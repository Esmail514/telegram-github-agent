from app.runner.approvals import BEFORE_COMMIT, LOW, ApprovalPolicy


def test_always_before_commit_requires_review_for_low_risk_changes():
    policy = ApprovalPolicy(mode="always_before_commit")

    assert policy.gate_required(BEFORE_COMMIT, LOW) is True
