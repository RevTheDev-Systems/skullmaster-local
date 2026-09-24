"""Diagnostics report smoke test (runs against the temp data dir)."""


def test_diagnostics_reports_all_sections(capsys):
    from app import diagnostics

    code = diagnostics.main()
    out = capsys.readouterr().out
    for section in (
        "Runtime",
        "Configuration",
        "Storage",
        "LLM backend",
        "TTS backend",
        "STT backend",
        "YouTube",
        "Server",
    ):
        assert section in out, section
    assert code in (0, 1)
    assert "PASS" in out
    # no credential material is ever printed
    assert "password" not in out.lower()
