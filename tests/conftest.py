def pytest_addoption(parser):
    parser.addoption("--run-docker", action="store_true", default=False, help="Run integration tests against the local analysis Docker image")


def pytest_collection_modifyitems(config, items):
    import pytest
    if not config.getoption("--run-docker"):
        skip = pytest.mark.skip(reason="Use --run-docker after building analysis-report:0.1")
        for item in items:
            if item.get_closest_marker("docker"):
                item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: uses the local Docker engine, without calling an LLM")
