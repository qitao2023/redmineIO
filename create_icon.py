"""生成应用图标 — 日报概念，醒目易识别。"""

from PIL import Image, ImageDraw

SIZE = 256


def create_icon():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── 圆角方形背景（深蓝）──
    draw.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=48, fill=(30, 58, 138))

    # ── 白色页面（模拟文档）──
    page_left = 56
    page_top = 40
    page_right = SIZE - 36
    page_bottom = SIZE - 36
    draw.rounded_rectangle(
        [page_left, page_top, page_right, page_bottom],
        radius=24, fill=(255, 255, 255)
    )

    # ── 页面顶部色条（标题栏感觉）──
    draw.rectangle(
        [page_left + 12, page_top + 20, page_right - 12, page_top + 44],
        fill=(37, 99, 235)
    )

    # ── 四行文字模拟线 ──
    line_colors = [
        (59, 130, 246),   # 蓝
        (16, 185, 129),   # 绿
        (239, 68, 68),    # 红
        (192, 38, 211),   # 洋红
    ]
    line_y = page_top + 72
    for i, color in enumerate(line_colors):
        # 小圆点
        dot_x = page_left + 28
        draw.ellipse([dot_x, line_y + 2, dot_x + 10, line_y + 12], fill=color)
        # 横线
        bar_w = [120, 100, 130, 90][i]
        draw.rounded_rectangle(
            [dot_x + 20, line_y + 4, dot_x + 20 + bar_w, line_y + 10],
            radius=3, fill=color
        )
        line_y += 30

    # ── 右下角大勾 ──
    cx = page_right - 30
    cy = page_bottom - 30
    r = 22
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(16, 185, 129))
    draw.line(
        [(cx - 8, cy), (cx - 2, cy + 8), (cx + 9, cy - 7)],
        fill=(255, 255, 255), width=4
    )

    # ── 保存 ico ──
    sizes = [256, 128, 64, 48, 32, 16]
    img.save("icon.ico", format="ICO", sizes=[(s, s) for s in sizes])

    # ── 同时存一个 PNG 预览 ──
    img.save("icon_preview.png")
    print("icon.ico + icon_preview.png 已生成")


if __name__ == "__main__":
    create_icon()
