from datetime import datetime


def log(message):

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    )