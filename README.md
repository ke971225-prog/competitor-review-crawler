# Competitor Review Crawler

GitHub project name: `competitor-review-crawler`

This project is a Python + Playwright crawler for competitor ecommerce research. It targets Shopify / WooCommerce independent stores, extracts product data, public customer reviews, ratings, review images, and product images, then exports structured CSV / JSON files.

Python + Playwright 抓取 Shopify / WooCommerce 独立站的商品、图片、评论和用户上传图片。

默认目标：

- `https://cutevision.shop/`
- `https://www.quboox.com/`

## 功能

- 自动读取 Shopify `products.json` 商品接口
- 识别 Shopify / WooCommerce 页面特征
- 自动识别 Judge.me / Loox / Yotpo / Stamped 评论系统
- 对 Loox 支持真实 iframe/API 分页抓取，而不是只抓页面首屏 DOM
- 用 Playwright 渲染商品页，支持 lazyload 图片、滚动加载和“Load more / Next”评论分页
- 提取产品标题、handle、URL、价格、rating、review count
- 提取评论作者、评分、标题、正文、日期、评论图
- 下载商品图片和评论图片
- 导出 CSV 和 JSON

## 安装

建议新建干净虚拟环境：

```powershell
cd G:\独立站新站\pixelcreat\tools\competitor_crawler
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 运行

```powershell
python crawler.py
```

输出目录：

- `output/products.csv`
- `output/reviews.csv`
- `output/data.json`
- `output/images/`

本仓库保留了一份当前抓取结果：

- `output_complete/products.csv`
- `output_complete/reviews.csv`
- `output_complete/data.json`

图片文件较大，默认不提交到 GitHub；运行脚本后会重新下载到本地 `output*/images/`。

指定站点：

```powershell
python crawler.py --site https://cutevision.shop/ --site https://www.quboox.com/ --output output
```

不下载图片：

```powershell
python crawler.py --no-images
```

如果当前机器还没有安装 Playwright Chromium，可以先导出商品和图片：

```powershell
python crawler.py --http-only
```

`--http-only` 不会抓取 JS 动态评论；安装 Chromium 后运行默认命令即可补全评论。

## 字段

`products.csv`：

- `site`
- `platform`
- `title`
- `handle`
- `url`
- `price`
- `compare_at_price`
- `currency`
- `rating`
- `review_count`
- `image_urls`
- `downloaded_images`

`reviews.csv`：

- `site`
- `provider`
- `product_title`
- `product_url`
- `author`
- `rating`
- `title`
- `body`
- `date`
- `image_urls`
- `downloaded_images`

## 说明

这个工具只抓取公开页面数据。评论抓取结果适合做竞品分析，不应直接伪造成本站 verified buyer 评论。
