import json
from datetime import datetime, timezone, timedelta

import scrapy
import html2text


def process_views(views):
    if "K" in views:
        views = int(float(views.replace("K", "")) * 1000)
    elif "M" in views:
        views = int(float(views.replace("M", "")) * 1000000)
    else:
        views = int(views)
    return views


def parse_post_url(url):
    url = url.split("?")[0]
    channel_id, post_id = url.split("/")[-2:]
    return {
        "url": url,
        "channel_id": channel_id.lower(),
        "post_id": int(post_id)
    }


def to_timestamp(dt_str):
    dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S+00:00")
    dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def html2text_setup():
    instance = html2text.HTML2Text(bodywidth=0)
    instance.ignore_links = True
    instance.ignore_images = True
    instance.ignore_tables = True
    instance.ignore_emphasis = True
    instance.ul_item_mark = ""
    return instance


class TelegramSpider(scrapy.Spider):
    name = "telegram"
    channel_url_template = "https://t.me/s/{}"
    post_url_template = "https://t.me/{}?embed=1"

    def __init__(self, *args, **kwargs):
        assert "channels_file" in kwargs
        with open(kwargs.pop("channels_file")) as r:
            self.channels = json.load(r)["channels"]
        self.html2text = html2text_setup()
        self.until_ts = int((datetime.now() - timedelta(hours=6)).timestamp())

        super().__init__(*args, **kwargs)

    def start_requests(self):
        urls = [self.channel_url_template.format(ch["name"]) for ch in self.channels]
        for url in urls:
            yield scrapy.Request(url=url, callback=self.parse_channel)

    def parse_channel(self, response):
        url = response.url
        history_path = "//body/main/div/section[contains(@class, 'tgme_channel_history')]/div"
        posts = response.xpath(history_path + "/div")

        min_post_id, min_post_ts = None, None
        for post in posts:
            post_path = post.xpath("@data-post")
            post_time = post.css("time.time::attr(datetime)")
            if not post_path or not post_time:
                continue
            post_path, post_time = post_path.get(), post_time.get()

            post_id = int(post_path.split("/")[-1])
            post_ts = to_timestamp(post_time)

            min_post_id = min(post_id, min_post_id) if min_post_id is not None else post_id
            min_post_ts = min(post_ts, min_post_ts) if min_post_ts is not None else post_ts

            post_url = self.post_url_template.format(post_path)
            try:
                item = self._parse_post(post, post_url)
                if item is None:
                    continue
                yield item
            except Exception as e:
                print(f"Unexpected error at {post_url}:", str(e))
                continue

        if not min_post_ts or min_post_ts < self.until_ts:
            return
        url = url.split("?")[0]
        url += "?before={}".format(min_post_id)
        yield scrapy.Request(url=url, callback=self.parse_channel)

    def _parse_post(self, post_element, post_url):
        text_path = "div.tgme_widget_message_bubble > div.tgme_widget_message_text"
        views_path = "span.tgme_widget_message_views::text"
        time_path = "time.time::attr(datetime)"
        images_path = "a.tgme_widget_message_photo_wrap::attr(style)"
        videos_path = "video.tgme_widget_message_video::attr(src)"
        reply_path = "a.tgme_widget_message_reply::attr(href)"
        forward_path = "a.tgme_widget_message_forwarded_from_name::attr(href)"

        item = parse_post_url(post_url)
        text_element = post_element.css(text_path)
        if not text_element:
            # Images only
            return None

        item["text"] = self._parse_html(text_element.extract_first())
        item["links"] = text_element.css("a::attr(href)").getall()
        item["fetch_time"] = int(datetime.now().replace(tzinfo=timezone.utc).timestamp())

        views_element = post_element.css(views_path)
        if not views_element:
            # Service messages
            return None

        item["views"] = process_views(views_element.get())

        time_element = post_element.css(time_path)
        item["pub_time"] = to_timestamp(time_element.get())

        item["images"] = []
        image_elements = post_element.css(images_path)
        for image_style in image_elements:
            image_style = image_style.get()
            for style in image_style.split(";"):
                style = style.strip()
                if "background-image" in style:
                    image_url = style.split("url(")[-1][1:-2]
                    item["images"].append(image_url)

        item["videos"] = []
        video_elements = post_element.css(videos_path)
        for video in video_elements:
            item["videos"].append(video.get())

        reply_element = post_element.css(reply_path)
        if reply_element:
            item["reply_to"] = reply_element.get()
        forward_element = post_element.css(forward_path)
        if forward_element:
            item["forward_from"] = forward_element.get()

        return item

    def _parse_html(self, html):
        text = self.html2text.handle(html)
        sentences = [s.strip() for s in text.strip().split("\n") if s.strip()]
        for i, sentence in enumerate(sentences):
            if sentence[-1].isalpha():
                sentences[i] = sentence + "."
        text = "\n".join(sentences)
        return text
