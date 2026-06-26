from __future__ import annotations

import unittest


class PromptGuardTests(unittest.TestCase):
    def test_extracts_title_font_constraint_from_commerce_prompt(self) -> None:
        from codex_image.prompt_guard import extract_prompt_constraints

        prompt = "产品目标人群是宝妈为主，文案标题设计偏儿童Q版卡通化，色彩偏淡彩，简约大气上档次，高级感"

        constraints = extract_prompt_constraints(prompt)

        self.assertIn("标题字体/标题设计：文案标题设计偏儿童Q版卡通化", constraints)
        self.assertIn("目标人群：宝妈为主", constraints)
        self.assertIn("色彩：偏淡彩", constraints)

    def test_build_prompt_guard_instructions_keeps_original_intent(self) -> None:
        from codex_image.prompt_guard import build_prompt_guard_instructions, extract_prompt_constraints

        prompt = "文案标题设计偏儿童Q版卡通化，限制：不要水印，不要品牌logo"
        constraints = extract_prompt_constraints(prompt)

        instructions = build_prompt_guard_instructions(constraints)

        self.assertIn("只能扩写用户提示词", instructions)
        self.assertIn("不得改变原意", instructions)
        self.assertIn("标题字体/标题设计：文案标题设计偏儿童Q版卡通化", instructions)
        self.assertIn("限制：不要水印，不要品牌logo", instructions)

    def test_build_original_prompt_instructions_forbids_rewriting(self) -> None:
        from codex_image.prompt_guard import build_original_prompt_instructions

        instructions = build_original_prompt_instructions()

        self.assertIn("原始提示词模式", instructions)
        self.assertIn("不得优化", instructions)
        self.assertIn("不得优化、扩写、翻译", instructions)
        self.assertIn("逐字使用", instructions)

    def test_preserves_current_prompt_mode_contract(self) -> None:
        from codex_image.prompt_guard import (
            build_prompt_guard_instructions,
            extract_prompt_constraints,
        )

        self.assertIn("不得改变原意", build_prompt_guard_instructions(["色彩：蓝色"]))
        self.assertEqual(
            ["目标人群：儿童", "色彩：蓝色", "限制：禁止出现文字"],
            extract_prompt_constraints("目标人群是儿童，色彩：蓝色，禁止出现文字"),
        )


if __name__ == "__main__":
    unittest.main()
