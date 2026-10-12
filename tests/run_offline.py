# Responsible for: explicitly selecting the non-network/non-IPC suite in restricted environments.

import sys
import unittest


BLOCKED_INTEGRATIONS = {
    "test_single_instance_activates_existing_owner",
    "test_single_instance_independent_roots_and_stale_file",
    "test_single_instance_crash_releases_os_lock",
    "test_http_models_completions_unicode_text_only",
    "test_http_status_errors_and_invalid_json",
    "test_connection_refused",
    "test_http_timeout_with_short_test_transport",
    "test_connection_ui_is_responsive_and_recovers_repeatedly",
    "test_pipeline_sends_transcript_not_recording",
}


def cases(suite):
    """Yield every discovered case without changing its assertions or result."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main():
    """Print every excluded integration and run the remaining unchanged tests."""
    selected = unittest.TestSuite()
    for case in cases(unittest.defaultTestLoader.discover("tests")):
        if case.id().rsplit(".", 1)[-1] in BLOCKED_INTEGRATIONS:
            print("NOT RUN (real IPC/loopback required): " + case.id(), flush=True)
        else:
            selected.addTest(case)
    print("Offline subset only; this is NOT a full integration-suite result.", flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(selected)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
