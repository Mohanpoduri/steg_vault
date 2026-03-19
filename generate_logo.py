from PIL import Image, ImageDraw, ImageFont

# Create a dark background image (400x400)
img = Image.new('RGB', (400, 400), color='#0b0f19')
draw = ImageDraw.Draw(img)

# Define the shield coordinates
shield_points = [(200, 40), (320, 100), (320, 260), (200, 340), (80, 260), (80, 100)]
draw.polygon(shield_points, outline='#00d8ff', width=8)

# Draw the internal data pixels
draw.rectangle([170, 140, 230, 200], fill='#00d8ff')  # Center Cyan
draw.rectangle([170, 220, 230, 250], fill='#ffffff')  # Bottom White
draw.rectangle([110, 140, 140, 170], fill='#a0a0a0')  # Left gray-ish
draw.rectangle([260, 140, 290, 170], fill='#a0a0a0')  # Right gray-ish

# Save the final image as a high-quality JPG
img.save('stegvault_logo.jpg', 'JPEG', quality=100)
print("Logo successfully saved as stegvault_logo.jpg!")
