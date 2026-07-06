from services.cover_designer import CoverDesigner

designer = CoverDesigner()

designer.create_cover(
    input_image="output/images/barnaby's_sweet_dreams.png",
    output_image="output/images/final_cover.png",
    title="Barnaby's Sweet Dreams",
)

print("Done!")