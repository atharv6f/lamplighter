"""One more test of orders.py, which the task does not show."""

import unittest

from orders import total_cents


class MoreTest(unittest.TestCase):
    def test_many(self) -> None:
        self.assertEqual(total_cents([(199, 3), (5, 10), (1, 0)]), 647)
