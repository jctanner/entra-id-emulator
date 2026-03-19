"""Entry point for running the Entra ID mock server."""

from entra_mock.app import create_app

app = create_app()

if __name__ == "__main__":
    config = app.config["ENTRA_CONFIG"]
    server = config["server"]
    app.run(
        host=server["host"],
        port=server["port"],
        debug=server.get("debug", False),
    )
