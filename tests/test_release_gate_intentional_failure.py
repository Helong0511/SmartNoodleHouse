def test_required_check_gate_rejects_intentional_failure():
    """Temporary negative control: this test must fail on the validation PR."""
    assert False, "intentional release-gate negative-control test; never merge"
