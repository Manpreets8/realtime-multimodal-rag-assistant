from app.rag.text_cleaning import clean_text

# Unusual characters are built from code points so they are visible in review.
FI_LIGATURE = chr(0xFB01)
FULLWIDTH_REPORT = "".join(chr(0xFF00 + ord(c) - 0x20) for c in "report")
NO_BREAK_SPACE = chr(0x00A0)
ZERO_WIDTH_SPACE = chr(0x200B)
SOFT_HYPHEN = chr(0x00AD)


def test_ligatures_and_fullwidth_are_normalised() -> None:
    assert clean_text(f"{FI_LIGATURE}nancial {FULLWIDTH_REPORT}") == "financial report"


def test_control_and_zero_width_characters_are_removed() -> None:
    text = f"pay{ZERO_WIDTH_SPACE}roll\x00 over{SOFT_HYPHEN}time\x07"

    assert clean_text(text) == "payroll overtime"


def test_whitespace_is_collapsed_but_paragraphs_kept() -> None:
    text = f"  First   line\t here \r\n\r\n\n\n  Second{NO_BREAK_SPACE}paragraph  "

    assert clean_text(text) == "First line here\n\nSecond paragraph"


def test_dehyphenation_is_opt_in() -> None:
    text = "The employ-\nment contract"

    assert clean_text(text, dehyphenate=True) == "The employment contract"
    assert clean_text(text) == "The employ-\nment contract"


def test_real_hyphens_within_a_line_are_kept() -> None:
    assert clean_text("A well-known state-of-the-art model", dehyphenate=True) == (
        "A well-known state-of-the-art model"
    )
