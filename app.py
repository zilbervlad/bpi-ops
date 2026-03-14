from app import create_app
from app.extensions import db

app = create_app()


@app.route("/create-db")
def create_db():
    db.create_all()
    return "Database tables created"


if __name__ == "__main__":
    app.run(debug=True)