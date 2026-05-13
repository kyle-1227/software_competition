from app.core.config import Settings


def test_frontend_origins_csv_builds_cors_origins() -> None:
    settings = Settings(
        frontend_origins="http://localhost:9001,http://127.0.0.1:9001"
    )

    assert (
        settings.frontend_origins
        == "http://localhost:9001,http://127.0.0.1:9001"
    )
    assert settings.cors_origins == [
        "http://localhost:9001",
        "http://127.0.0.1:9001",
    ]


def test_frontend_origins_trims_spaces_and_ignores_empty_items() -> None:
    settings = Settings(
        frontend_origins="http://localhost:9001, http://127.0.0.1:9001, "
    )

    assert settings.cors_origins == [
        "http://localhost:9001",
        "http://127.0.0.1:9001",
    ]
