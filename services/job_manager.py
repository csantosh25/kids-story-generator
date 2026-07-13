class JobManager:

    status = {
        "step": "Waiting...",
        "progress": 0,
    }

    @classmethod
    def update(cls, step, progress):
        cls.status = {
            "step": step,
            "progress": progress,
        }

    @classmethod
    def get(cls):
        return cls.status