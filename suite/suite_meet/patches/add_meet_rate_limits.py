from suite.suite_meet.install import add_rate_limits


def execute():
    """Execute patch to add dynamic rate limits for Meet endpoints."""
    add_rate_limits()
