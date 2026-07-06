from pipelines.story_pipeline import StoryPipeline

topic = input("Enter story theme: ")

pipeline = StoryPipeline()

pipeline.run(topic)