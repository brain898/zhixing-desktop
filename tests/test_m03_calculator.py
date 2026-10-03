"""M03 AC08：复算器独立测试，不导入数据库或模型。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from consult_calculator import evaluate_expression, recompute_calculations


class TestCalculator(unittest.TestCase):
    def test_ac08_dates_and_arithmetic(self):
        for expression, result in {
            '(12 + 3) * 2 / 5 - 1': 5,
            "minutes_between('2026-10-01 09:00', '2026-10-01 09:12')": 12,
            "hours_between('2026-10-01 09:00:00', '2026-10-01 11:30:00')": 2.5,
            "days_between('2026-10-01', '2026-10-03')": 2,
            '-2 + +3': 1,
        }.items():
            with self.subTest(expression=expression):
                self.assertAlmostEqual(evaluate_expression(expression), result)

    def test_ac08_malicious_syntax_and_unknown_functions(self):
        for expression in ("__import__('os').system('echo unsafe')", '(1).__class__', 'abs(-1)',
                           'sum([1,2])', '2 ** 999999', '[x for x in (1,2)]', 'True+1',
                           'lambda:1', "open('x')", "minutes_between.__call__('x','y')",
                           '(x:=1)', '{1:2}[1]', '1//2', '1%2', '1<2', '"abc"',
                           "minutes_between(a='2026-10-01',b='2026-10-02')"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                evaluate_expression(expression)

    def test_ac08_invalid_dates_and_bounded_size(self):
        for expression in ('1/0', '1e999', '1e100*10', '+' * 2001,
                           "minutes_between('2026-02-31','2026-10-01')",
                           "minutes_between('2026-10-01T09:00','2026-10-01')", '+'.join(['1']*150)):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                evaluate_expression(expression)

    def test_ac08_tolerance_and_overwrite(self):
        rows, corrected = recompute_calculations([
            {'expression':'1','model_result':1.009,'output_key':'a'},
            {'expression':'1000','model_result':1004,'output_key':'b'},
            {'expression':'1000','model_result':1010,'output_key':'c'},
            {'expression':'unknown(1)','model_result':1,'output_key':'d'},
        ], {'a':1.009,'b':1004,'c':1010,'d':1})
        self.assertEqual([r['status'] for r in rows], ['一致','一致','已按程序复算更正','未经复算'])
        self.assertEqual(corrected, {'a':1.009,'b':1004,'c':1000,'d':1})

    def test_ac08_undeclared_output_not_added(self):
        _, corrected = recompute_calculations([{'expression':'1+1','model_result':3,'output_key':'alien'}], {})
        self.assertEqual(corrected, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
