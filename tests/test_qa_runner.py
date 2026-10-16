import importlib.util
from pathlib import Path


def test_qa_summary_does_not_count_skips_or_errors_as_passed(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'tools' / 'run_qa.py'
    spec = importlib.util.spec_from_file_location('qa_runner', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = tmp_path / 'junit.xml'
    report.write_text('<testsuites><testsuite><testcase/>'
                      '<testcase><skipped/></testcase><testcase><failure/></testcase>'
                      '<testcase><error/></testcase></testsuite></testsuites>')
    assert module.summarize_junit(report) == {'total': 4, 'passed': 1, 'failed': 2, 'skipped': 1}
