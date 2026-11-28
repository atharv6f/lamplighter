"""Tests of orders.py."""

import unittest

from orders import total_cents


class TotalTest(unittest.TestCase):
    def test_one_of_each(self) -> None:
        self.assertEqual(total_cents([(250, 1), (100, 1)]), 350)

    def test_quantities(self) -> None:
        self.assertEqual(total_cents([(250, 2), (100, 3)]), 800)

    def test_no_lines(self) -> None:
        self.assertEqual(total_cents([]), 0)


if __name__ == "__main__":
    unittest.main()
