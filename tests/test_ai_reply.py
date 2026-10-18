"""QQ AI Markdown image rendering, safe content, paging and delivery fallback."""
import base64
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image
from firefly import config as settings
from firefly.render import ai_reply


class MarkdownTests(unittest.TestCase):
    def test_common_markdown_is_typeset(self):
        html=ai_reply.build_ai_reply_html('# 收支分析\n\n**结余**\n\n- 收入\n- 支出\n\n> 注意预算\n\n|项目|金额|\n|---|---|\n|收入|100|\n\n```python\nprint(1)\n```')
        for token in ('<h1>收支分析</h1>','<strong>结余</strong>','<ul>','<blockquote>','<table>','<pre><code'):
            self.assertIn(token,html)
    def test_raw_html_remote_images_and_active_links_are_disabled(self):
        html=ai_reply.build_ai_reply_html('<script src="https://evil.test/x.js">x</script>\n\n![remote](https://evil.test/image.png)\n\n[link](https://evil.test)\n\n<img src="file:///etc/passwd" onerror="x">')
        self.assertNotIn('<script',html);self.assertNotIn('<a ',html)
        self.assertNotIn('<img src="https:',html);self.assertNotIn('<img src="file:',html)
        self.assertIn("default-src 'none'",html);self.assertIn('图片：remote',html)
    def test_code_and_title_cannot_escape_their_elements(self):
        html=ai_reply.build_ai_reply_html('```html\n</code><script>alert(1)</script>\n```',title='<img src=x onerror=x>')
        self.assertNotIn('<script>',html);self.assertNotIn('<img src=x',html)
        self.assertIn('&lt;script&gt;',html)
    def test_long_content_has_visible_bounds_notice(self):
        html=ai_reply.build_ai_reply_html(('line\n'*(ai_reply.MAX_LINES+1))+'END_SENTINEL')
        self.assertIn('内容较长',html);self.assertNotIn('END_SENTINEL',html)
        html=ai_reply.build_ai_reply_html('a'*(ai_reply.MAX_CHARS+1))
        self.assertIn('内容较长',html)
    def test_pages_preserve_every_pixel_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'reply.png';Image.new('RGB',(300,1250),'white').save(path)
            pages=ai_reply._split_png(path,400)
        images=[Image.open(io.BytesIO(page)) for page in pages]
        self.assertGreater(len(images),1);self.assertEqual(sum(p.height for p in images),1250)
        self.assertTrue(all(p.width==300 and p.height<=400 for p in images))
        for image in images:image.close()
    def test_page_limit_rejects_excessive_images(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'reply.png';Image.new('RGB',(300,ai_reply.MAX_PAGES*20+1),'white').save(path)
            with self.assertRaises(ValueError):ai_reply._split_png(path,20)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old=settings._CONFIG_CACHE;settings._CONFIG_CACHE=settings.Config(push={'image_max_height':1000})
    async def asyncTearDown(self):settings._CONFIG_CACHE=self.old
    async def test_render_cleans_temporary_files_after_success(self):
        paths=[]
        async def fake(html,*,output_path,**options):
            self.assertEqual(options['width'],880);self.assertEqual(options['scale'],1.5)
            paths.append(output_path);Image.new('RGB',(300,2500),'white').save(output_path);return output_path
        with patch.object(ai_reply,'render_html_to_image',new_callable=AsyncMock,side_effect=fake):pages=await ai_reply.render_ai_reply('# hi')
        self.assertEqual(len(pages),3);self.assertFalse(paths[0].parent.exists())
    async def test_render_cleans_temporary_files_after_failure(self):
        paths=[]
        async def fake(html,*,output_path,**options):
            paths.append(output_path);output_path.write_bytes(b'partial');raise RuntimeError('browser unavailable')
        with patch.object(ai_reply,'render_html_to_image',new_callable=AsyncMock,side_effect=fake):
            with self.assertRaises(RuntimeError):await ai_reply.render_ai_reply('hi')
        self.assertFalse(paths[0].parent.exists())
    async def test_paginated_images_use_base64_in_the_original_sender(self):
        send=AsyncMock()
        with patch.object(ai_reply,'render_ai_reply',new_callable=AsyncMock,return_value=[b'first-image',b'second-image']):r=await ai_reply.send_ai_reply(send,'**hi**')
        self.assertEqual(r['images_sent'],2);self.assertFalse(r['text_fallback'])
        for expected,call in zip([b'first-image',b'second-image'],send.call_args_list):
            segment=call.args[0][0];self.assertEqual(segment.type,'image')
            value=segment.data['file'];self.assertTrue(value.startswith('base64://'))
            self.assertEqual(base64.b64decode(value.removeprefix('base64://')),expected)
    async def test_renderer_failure_falls_back_to_literal_complete_text(self):
        send=AsyncMock();text='[CQ:at,qq=all]\n'+'完整文字\n'*1000
        with patch.object(ai_reply,'render_ai_reply',new_callable=AsyncMock,side_effect=RuntimeError('failed')):r=await ai_reply.send_ai_reply(send,text)
        self.assertTrue(r['text_fallback']);self.assertEqual(r['images_sent'],0)
        messages=[call.args[0] for call in send.call_args_list]
        self.assertTrue(all(m[0].type=='text' for m in messages))
        restored=''.join(m[0].data['text'] for m in messages)
        self.assertTrue(restored.endswith(text));self.assertTrue(all(len(m[0].data['text'])<=3500 for m in messages))
    async def test_image_send_failure_falls_back_without_restarting_images(self):
        send=AsyncMock(side_effect=[{'message_id':1},RuntimeError('QQ reject'),{'message_id':2}])
        with patch.object(ai_reply,'render_ai_reply',new_callable=AsyncMock,return_value=[b'one',b'two']):r=await ai_reply.send_ai_reply(send,'完整回复')
        self.assertEqual(r['images_sent'],1);self.assertTrue(r['text_fallback'])
        self.assertEqual(send.call_args_list[-1].args[0][0].type,'text')
    async def test_explicit_ai_text_delivery_overrides_rule_summary_switch(self):
        from firefly.render import bot
        settings.get_config().push.send_summary_text=False
        fake=NS(send_private_msg=AsyncMock(),send_group_msg=AsyncMock())
        with patch.object(bot,'_pick_bot',return_value=fake):
            result=await bot.broadcast_summary('AI 解读',target_type='private',target_id='123',text_enabled=True)
            default=await bot.broadcast_summary('普通规则摘要',target_type='private',target_id='123')
        self.assertTrue(result['text_sent']);self.assertFalse(default['text_sent']);fake.send_private_msg.assert_awaited_once()
    async def test_handler_renders_successful_ai_and_finishes_without_duplicate_text(self):
        import nonebot
        try:nonebot.get_driver()
        except ValueError:nonebot.init(driver='~fastapi',_env_file=None)
        from firefly.plugins import chat as plugin
        from firefly.ai import tool_query
        from firefly.db import session as db
        class Scope:
            async def __aenter__(self):return NS(commit=AsyncMock())
            async def __aexit__(self,*args):pass
        event=NS(get_plaintext=lambda:'ai 分析',get_session_id=lambda:'private_123',get_user_id=lambda:'123')
        with patch.object(plugin,'_event_user',new_callable=AsyncMock,return_value=NS(id=7)),patch.object(db,'session_scope',return_value=Scope()),patch.object(tool_query,'query_with_tools',new_callable=AsyncMock,return_value={'answer':'# AI答复','engine':'llm','tools':[]}),patch('firefly.services.audit.log_action',new_callable=AsyncMock),patch.object(ai_reply,'send_ai_reply',new_callable=AsyncMock) as deliver,patch.object(plugin.record,'finish',new_callable=AsyncMock) as finish:
            await plugin._handle_message(NS(self_id='456'),event)
        deliver.assert_awaited_once();self.assertEqual(deliver.call_args.args[1],'# AI答复');self.assertEqual(deliver.call_args.args[0],plugin.record.send)
        finish.assert_awaited_once_with()


if __name__=='__main__':unittest.main()
