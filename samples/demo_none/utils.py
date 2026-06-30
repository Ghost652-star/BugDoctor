def get_data():
    try:
        return fetch_value()
    except ValueError:
        return None


def fetch_value() -> str:
    raise ValueError("simulated failure")
