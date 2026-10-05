import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
from support import ROOT

spec = importlib.util.spec_from_file_location('report_wrapper', ROOT / 'scripts/run-report.py')
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


class WrapperTests(unittest.TestCase):
    def test_task_selection_aliases_cannot_bypass_final_report(self):
        for arguments in [ ['-tconfig'], ['-lhost'], ['--tag=config'], ['--skip-tag=config'],
                           ['--start-at', 'task'], ['--list-task'], ['--step'] ]:
            argv = ['run-report.py', '-i', 'examples/inventory/hosts.yml'] + arguments
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', argv), patch.object(wrapper.subprocess, 'run', side_effect=AssertionError('Unsafe playbook started')), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                wrapper.main()
            self.assertEqual(error.exception.code, 2)
