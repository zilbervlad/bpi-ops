from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
    @app.route("/create-db")
def create_db():
    from app.extensions import db
    db.create_all()
    return "Database tables created"