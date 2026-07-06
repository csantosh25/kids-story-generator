class EmailAgent:

    def generate(self, story, assets):

        print("📧 Preparing email...")

        print(story.email.subject)

        print(story.email.preview)

        # Gmail integration comes later

        return True