from __future__ import annotations

import unittest

from app.engines import trainer


class TrainerProgressTest(unittest.TestCase):
    def test_epoch_number_from_training_log(self) -> None:
        self.assertEqual(trainer._epoch_number("Epoch: 3"), 3)
        self.assertEqual(trainer._epoch_number("epoch 15/15"), 15)
        self.assertEqual(trainer._epoch_number("loss 1.25"), 0)

    def test_training_defaults_match_official_v4_webui(self) -> None:
        self.assertEqual(trainer.S2_EPOCHS, 2)
        self.assertEqual(trainer.S1_EPOCHS, 15)
        self.assertEqual(trainer.LORA_RANK, 32)


if __name__ == "__main__":
    unittest.main()
