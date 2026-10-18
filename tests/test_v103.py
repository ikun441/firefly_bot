"""v1.0.3: key precedence, image/text AI delivery and read-only function calling."""
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from firefly import config as settings
from firefly.ai import tool_query, summarizer
from firefly.render import push, html


class ConfigurationTests(unittest.TestCase):
    def load(self, llm, env=None):
        with patch.object(settings, '_CONFIG_CACHE', None), patch.object(settings, '_load_env'), patch.object(settings, 'read_yaml_config', return_value={'llm': llm}), patch.dict(os.environ, env or {}, clear=True):
            return settings.load_config()
    def test_yaml_key_is_preserved(self):
        cfg=self.load({'api_key':'yaml-secret'})
        self.assertEqual(cfg.llm.api_key,'yaml-secret')
        self.assertNotIn('yaml-secret',json.dumps(cfg.model_dump_safe()))
    def test_environment_overrides_yaml_and_custom_environment(self):
        self.assertEqual(self.load({'api_key':'yaml-secret'}, {'LLM_API_KEY':'env-secret'}).llm.api_key,'env-secret')
        self.assertEqual(self.load({'api_key_env':'CUSTOM_KEY','api_key':'yaml-secret'}, {'CUSTOM_KEY':'custom-secret','LLM_API_KEY':'default-secret'}).llm.api_key,'custom-secret')
    def test_literal_key_in_old_env_field_is_compatible_and_redacted(self):
        cfg=self.load({'api_key_env':'sk-legacy-secret'})
        self.assertEqual(cfg.llm.api_key,'sk-legacy-secret')
        self.assertEqual(cfg.llm.api_key_env,'LLM_API_KEY')
        self.assertNotIn('sk-legacy-secret',json.dumps(cfg.model_dump_safe()))
        direct=settings.Config(llm={'api_key_env':'sk-legacy-secret'})
        self.assertNotIn('sk-legacy-secret',json.dumps(direct.model_dump_safe()))
    def test_env_name_is_not_used_as_a_key_and_disabled_stays_disabled(self):
        self.assertFalse(self.load({'api_key_env':'MISSING_KEY'}).llm.is_ready)
        self.assertFalse(self.load({'enabled':False,'api_key':'yaml-secret'}).llm.is_ready)


def report():
    return NS(id=123,title='测试报告',period='monthly',created_at=None,elapsed_ms=0,summary_text='规则摘要',payload={'meta':{'period_label':'测试期间'},'overview':{'income':100,'expense':38,'net':62},'budget':{'has_budget':True,'total_budget':100,'total_actual':38,'total_usage_ratio':38,'items':[]}})


class PushTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old=settings._CONFIG_CACHE
        settings._CONFIG_CACHE=settings.Config(push={'target_id':'123','send_summary_text':False})
        self.tmp=tempfile.TemporaryDirectory(prefix='firefly-v103-push-')
        self.paths=patch.object(settings,'DATA_DIR',Path(self.tmp.name));self.paths.start()
        self.summary=patch.object(summarizer,'summarize_report',new_callable=AsyncMock,return_value={'text':'AI 解读 <script>unsafe</script>','engine':'llm','elapsed_ms':2});self.summary_mock=self.summary.start()
        async def image(content,**kwargs):
            self.image_html=content
            path=kwargs['output_path'];path.write_bytes(b'test');return path
        self.image=patch.object(push,'render_html_to_image',new=AsyncMock(side_effect=image));self.image.start()
        self.shrink=patch.object(push,'shrink_for_qq',side_effect=lambda path:path);self.shrink.start()
        self.sender=patch.object(push.bot_module,'broadcast_summary',new_callable=AsyncMock,return_value={'image_sent':True,'text_sent':False});self.send=self.sender.start()
    async def asyncTearDown(self):
        self.sender.stop();self.shrink.stop();self.image.stop();self.summary.stop();self.paths.stop();settings._CONFIG_CACHE=self.old;self.tmp.cleanup()
    async def test_image_only_contains_ai_even_when_rule_text_disabled(self):
        r=await push.push_report(report(),content_type='image',ai_summary=True)
        self.assertTrue(r['ai_generated']);self.assertTrue(r['ai_delivered'])
        self.assertIn('data-section="ai-summary"',self.image_html)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;',self.image_html)
        self.assertNotIn('<script>unsafe</script>',self.image_html)
        self.assertEqual(self.send.call_args.args[0],'')
        self.assertEqual(self.summary_mock.call_args.kwargs['max_tokens'],settings.get_config().push.ai_max_tokens)
        self.assertIn('38.0%',self.image_html);self.assertNotIn('3800.0%',self.image_html)
    async def test_text_only_ai_does_not_require_rules_or_image(self):
        self.send.return_value={'text_sent':True,'image_sent':False}
        r=await push.push_report(report(),content_type='text',ai_summary=True)
        self.assertTrue(r['ai_delivered']);self.assertIn('AI 解读',self.send.call_args.args[0])
        self.assertTrue(self.send.call_args.kwargs['text_enabled'])
        push.render_html_to_image.assert_not_called()
    async def test_ai_failure_returns_partial_delivery_warning(self):
        self.summary_mock.return_value={'text':'','engine':'fallback','error':'认证失败'}
        r=await push.push_report(report(),content_type='image',ai_summary=True)
        self.assertTrue(r['success']);self.assertFalse(r['ai_generated']);self.assertFalse(r['ai_delivered']);self.assertIn('认证失败',r['errors'][0])
        self.assertNotIn('data-section="ai-summary"',self.image_html)
    async def test_image_failure_falls_back_to_ai_text(self):
        push.render_html_to_image.side_effect=RuntimeError('failed')
        self.send.return_value={'text_sent':True,'image_sent':False}
        r=await push.push_report(report(),content_type='image',ai_summary=True)
        self.assertTrue(r['text_fallback']);self.assertTrue(r['ai_delivered']);self.assertIn('AI 解读',self.send.call_args.args[0])
    async def test_disabled_ai_is_not_generated_and_artifacts_do_not_collide(self):
        a=await push.push_report(report(),content_type='image',ai_summary=False)
        b=await push.push_report(report(),content_type='image',ai_summary=False)
        self.summary_mock.assert_not_called();self.assertNotEqual(a['image_path'],b['image_path'])
    async def test_html_contains_uploaded_logo(self):
        self.assertIn('alt="流萤 Logo"',html.build_report_html(report()))
        self.assertIn('data:image/jpeg;base64,',html.build_report_html(report()))


def call(name,args='{}',ident='call1'):
    return NS(id=ident,function=NS(name=name,arguments=args))
def response(calls=None,text=None):
    return NS(choices=[NS(message=NS(tool_calls=calls,content=text))])


class ToolCallingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old=settings._CONFIG_CACHE;settings._CONFIG_CACHE=settings.Config(llm={'api_key':'test-key'})
        self.client=NS(chat=NS(completions=NS(create=AsyncMock())),close=AsyncMock())
        self.factory=patch.object(tool_query,'get_client',return_value=self.client);self.factory.start()
    async def asyncTearDown(self):
        self.factory.stop();settings._CONFIG_CACHE=self.old
    def test_prefix_requires_separator_and_is_case_insensitive(self):
        self.assertEqual(tool_query.explicit_question('AI 本月花多少'),'本月花多少')
        self.assertEqual(tool_query.explicit_question('ai：余额'),'余额')
        self.assertEqual(tool_query.explicit_question('ai'),'')
        self.assertIsNone(tool_query.explicit_question('airpods 100元'))
    async def test_function_result_is_returned_to_model(self):
        self.client.chat.completions.create.side_effect=[response([call('read_sum_total')]),response(text='本月支出38元')]
        with patch.object(tool_query,'execute_read_tool',new_callable=AsyncMock,return_value={'data':{'expense':38},'answer':'支出38'}) as read:
            result=await tool_query.query_with_tools(None,user_id=7,question='本月支出')
        self.assertTrue(result['read_only']);self.assertEqual(result['tools'],['read_sum_total'])
        self.assertEqual(read.call_args.kwargs['user_id'],7)
        self.assertIn('38',self.client.chat.completions.create.call_args.kwargs['messages'][-1]['content'])
        self.assertTrue(all(t['function']['name'].startswith('read_') for t in self.client.chat.completions.create.call_args.kwargs['tools']))
        self.client.close.assert_awaited_once()
    async def test_writes_unknown_functions_and_other_identity_are_blocked(self):
        for name,args in [('delete_transactions',{}),('read_sum_total',{'user_id':99}),('read_sum_total',{'sql':'SELECT *'}),('read_sum_total',{'custom_days':99999})]:
            with self.assertRaises(ValueError):
                await tool_query.execute_read_tool(None,user_id=7,name=name,arguments=args,question='测试')
    async def test_malformed_tool_arguments_are_not_executed(self):
        self.client.chat.completions.create.side_effect=[response([call('read_sum_total','not json')]),response(text='请补充问题')]
        with patch.object(tool_query,'execute_read_tool',new_callable=AsyncMock) as read:
            result=await tool_query.query_with_tools(None,user_id=7,question='问题')
        read.assert_not_called();self.assertTrue(result['read_only'])
        self.assertIn('false',self.client.chat.completions.create.call_args.kwargs['messages'][-1]['content'])
    async def test_tool_budget_stops_unbounded_calls(self):
        self.client.chat.completions.create.return_value=response([call('read_sum_total')])
        with patch.object(tool_query,'execute_read_tool',new_callable=AsyncMock,return_value={'data':{}}):
            result=await tool_query.query_with_tools(None,user_id=7,question='问题')
        self.assertLessEqual(len(result['tools']),8);self.assertLessEqual(self.client.chat.completions.create.await_count,5)
        self.assertEqual(result['engine'],'unavailable')
    async def test_explicit_ai_bypasses_pending_bookkeeping(self):
        import nonebot
        try:nonebot.get_driver()
        except ValueError:nonebot.init(driver='~fastapi',_env_file=None)
        from nonebot.adapters.onebot.v11 import PrivateMessageEvent, Message
        from firefly.plugins import chat as plugin
        from firefly.services import bot_conversation
        from firefly.db import session as db
        class Scope:
            async def __aenter__(self):return NS(commit=AsyncMock())
            async def __aexit__(self,*args):pass
        event=PrivateMessageEvent.model_validate({'time':1,'self_id':456,'post_type':'message','message_type':'private','sub_type':'friend','user_id':123,'message_id':7,'message':Message('ai 接受，帮我记账38元'),'original_message':Message('ai 接受，帮我记账38元'),'raw_message':'ai 接受，帮我记账38元','font':0,'sender':{'user_id':123}})
        with patch.object(plugin,'_event_user',new_callable=AsyncMock,return_value=NS(id=7)),patch.object(db,'session_scope',return_value=Scope()),patch.object(tool_query,'query_with_tools',new_callable=AsyncMock,return_value={'answer':'只允许查询','tools':[]}) as query,patch.object(bot_conversation,'handle_message',new_callable=AsyncMock) as book,patch.object(plugin.record,'finish',new_callable=AsyncMock),patch('firefly.services.audit.log_action',new_callable=AsyncMock):
            await plugin._handle_message(NS(self_id='456'),event)
        query.assert_awaited_once();book.assert_not_called()

class ToolAPIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_v102 import RegressionTests
        await RegressionTests.asyncSetUp(self)
    async def asyncTearDown(self):
        from tests.test_v102 import RegressionTests
        await RegressionTests.asyncTearDown(self)
    async def test_tool_reads_real_current_user_data(self):
        from tests.test_v102 import RegressionTests
        from firefly.db import session as db
        from firefly.services import ledger
        await RegressionTests.create(self,amount=38)
        async with db.session_scope() as session:
            await ledger.create_transaction(session,user_id=self.other_id,amount=999,memo='不能读取其他用户')
        settings.get_config().llm.enabled=True;settings.get_config().llm.api_key='test-key'
        fake=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=[response([call('read_sum_total')]),response(text='支出38元')]))),close=AsyncMock())
        with patch.object(tool_query,'get_client',return_value=fake):
            result=(await self.client.post('/api/ai/chat',json={'question':'ai 本月支出多少'})).json()
        self.assertTrue(result['data']['read_only'])
        self.assertEqual(result['data']['data']['results'][0]['data']['expense'],38)
        self.assertNotIn('999',fake.chat.completions.create.call_args.kwargs['messages'][-1]['content'])
        self.assertEqual((await self.client.get('/api/transactions')).json()['total'],1)
    async def test_explicit_ai_never_accepts_existing_draft(self):
        from firefly.db import session as db
        from firefly.db.models import BotDraft
        from firefly.services import bot_conversation
        from sqlmodel import select
        async with db.session_scope() as session:
            await bot_conversation.handle_message(session,user_id=self.user_id,key=self.key,text='打车38元')
        settings.get_config().llm.enabled=True;settings.get_config().llm.api_key='test-key'
        fake=NS(chat=NS(completions=NS(create=AsyncMock(return_value=response(text='此入口只查询，不执行记账。')))),close=AsyncMock())
        with patch.object(tool_query,'get_client',return_value=fake):
            await self.client.post('/api/ai/chat',json={'question':'ai 接受'})
        async with db.session_scope() as session:
            draft=(await session.exec(select(BotDraft).where(BotDraft.session_key==self.key))).first()
            self.assertEqual(draft.state,'pending')
        self.assertEqual((await self.client.get('/api/transactions')).json()['total'],0)


if __name__=='__main__':unittest.main()
