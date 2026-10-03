import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from deepseek_extractor import validate_and_sanitize_atoms, extract_atoms_via_deepseek_batched, _extract_metric_metadata
from structured_quality import structural_quality_flags, filter_resolved_relation_flags
from deepseek_extractor import classify_atom_issues

def block(text, index=0, path='物业 / 测试'):
    return {'id': f'sb_{index}', 'block_index': index, 'text_content': text,
            'block_type': 'paragraph', 'heading_path': path, 'paragraph_anchor': f'p_{index}'}

def atom(text, **kwargs):
    source=block(text)
    data=dict(title='样例', primary_category='制度与标准', atom_type='规则',
              subject='值班人员', statement='按规定处置', conditions=[], actions=[],
              exceptions=[], metric_definition=None,
              source_evidence=[{'field_name':'statement','source_block_id':source['id'],'excerpt':text}])
    data.update(kwargs)
    return data, source

def metrics(*rows):
    return {'name':'试验验收', 'unit':'', 'period':'', 'criteria':'', 'rows':list(rows)}

def row(name, relation='', value='', unit='', condition='', period='', linkage='', note=''):
    return dict(name=name, relation=relation, value=value, unit=unit, condition=condition,
                period=period, linkage=linkage, note=note)

class TestStructuredReview(unittest.TestCase):
    def test_extract_metric_metadata_no_fake_criteria(self):
        metric_source='承压不低于0.8 MPa，并保持30分钟，检查完成后须记录。'
        meta = _extract_metric_metadata(metric_source, '测试')
        self.assertEqual(meta['criteria'], '')
        self.assertEqual(meta['rows'], [])
        self.assertEqual(meta['name'], '测试')

    def test_concise_text_and_short_repeat_allowed(self):
        data, source=atom('漏水时须关阀。', statement='漏水时须关阀。',
                          conditions=['漏水时'], actions=['关阀'], exceptions=[])
        self.assertNotIn('结构化重复', ' '.join(structural_quality_flags(data)))
        self.assertEqual(validate_and_sanitize_atoms([data],[source],'v1')[0]['statement'],'漏水时须关阀。')

    def test_long_duplicate_detected_and_blocks(self):
        source='发现管道漏水时，值班人员应关闭相关阀门并通知工程主管，无法安全接近时不得进入现场。'
        data, b=atom(source, statement=source, conditions=[source], actions=[source], exceptions=[source])
        result=validate_and_sanitize_atoms([data],[b],'v1')[0]
        self.assertTrue(any('结构化重复' in v for v in result['quality_flags']))
        self.assertFalse(result['issues_summary']['deterministic_errors'])
        self.assertTrue(result['issues_summary']['model_doubts'])
        self.assertFalse(result['issues_summary']['model_doubts'][0]['blocking'])

    def test_disambiguated_condition_action_exception(self):
        text='发现管道漏水时，值班人员应关闭阀门并通知工程主管；无法安全接近时不得进入现场。'
        data,b=atom(text, statement='管道漏水处置',
                    conditions=['发现管道漏水时'], actions=['值班人员关闭阀门','通知工程主管'],
                    exceptions=['无法安全接近时不得进入现场'])
        flags=structural_quality_flags(data)
        self.assertNotIn('结构化重复', ' '.join(flags))
        self.assertNotIn('例外对应需核对', ' '.join(flags))

    def test_metric_related_multirow_and_no_statistical_period(self):
        source='承压不低于0.8 MPa，并保持30分钟。'
        data,b=atom(source,primary_category='指标数据',atom_type='指标',
            statement='承压验收',metric_definition=metrics(
              row('压力','不低于','0.8','MPa'),
              row('持续时间','','30','分钟',linkage='在上述压力条件下保持')))
        self.assertEqual(structural_quality_flags(data),[])
        self.assertEqual(data['metric_definition']['rows'][0]['period'],'')
        self.assertEqual(data['metric_definition']['rows'][1]['linkage'],'在上述压力条件下保持')

    def test_unrelated_metrics_range_and_reporting_period(self):
        source='月度达标率不少于95%，温度控制在18至26℃。'
        data,b=atom(source,primary_category='指标数据', atom_type='指标',
            metric_definition=metrics(
              row('月度达标率','不少于','95','%',period='月度'),
              row('温度范围','控制在','18至26','℃')))
        self.assertNotIn('指标关联需核对',' '.join(structural_quality_flags(data)))

    def test_semantic_limit_expression_and_legacy_warning(self):
        # “严格控制在3秒以内”与本行“不超过3秒”一致，不应制造字面不对应红字。
        source='机电顾问团队对双回路电源自动切换装置执行带载切换测试，切换时间必须严格控制在3秒以内。'
        check=metrics(row('切换时间','不超过','3','秒'))
        data,_=atom(source,primary_category='指标数据',atom_type='指标',
                    statement='带载切换时间不超过3秒',metric_definition=check)
        flags=structural_quality_flags(data)
        self.assertFalse(any('比较关系' in flag for flag in flags), flags)
        legacy=['指标关联需核对：第1行的比较关系「不超过」未在证据摘录中直接对应',
                '信息遗漏需核对：其他问题']
        self.assertEqual(filter_resolved_relation_flags(legacy,check,data['source_evidence']),
                         ['信息遗漏需核对：其他问题'])

    def test_equivalent_relation_needs_matching_number_nearby(self):
        source='备用设备启动时间控制在3秒以内，但其他设备时间为9秒。'
        good,_=atom(source,primary_category='指标数据',
                    metric_definition=metrics(row('启动时间','不超过','3','秒')))
        self.assertFalse(any('比较关系' in f for f in structural_quality_flags(good)))
        wrong,_=atom(source,primary_category='指标数据',
                     metric_definition=metrics(row('其他设备时间','不超过','9','秒')))
        self.assertTrue(any('比较关系' in f for f in structural_quality_flags(wrong)))

    def test_machine_doubt_allows_single_review_not_unchecked_batch(self):
        issues=classify_atom_issues(
            ['结构化未完成：模型抽取不足', '指标关联需核对：比较关系字面不同',
             '疑似规则冲突待人工复核'], {}, ['p1'])
        self.assertFalse(issues['deterministic_errors'])
        self.assertTrue(all(not x['blocking'] for x in issues['model_doubts']))
        invalid=classify_atom_issues(['伪造来源：来源块不存在'],{},['p1'])
        self.assertTrue(invalid['deterministic_errors'][0]['blocking'])
        mismatch=classify_atom_issues(['来源摘录与原文块不匹配：测试错误'],{},['p1'])
        self.assertTrue(mismatch['deterministic_errors'][0]['blocking'])

    def test_confirmation_eligibility_manual_doubts_vs_source_integrity(self):
        import json
        from main import _evaluate_review_eligibility
        source='机电顾问团队须严格控制切换时间在3秒以内。'
        definition=metrics(row('切换时间','不超过','3','秒'))
        version={
            'title':'双回路切换测试','statement':'切换时间不超过3秒并记录测试结果',
            'primary_category':'指标数据','quality_flags_json':json.dumps([
                '指标关联需核对：第1行的比较关系「不超过」未在证据摘录中直接对应'
            ],ensure_ascii=False),'metric_definition_json':json.dumps(definition,ensure_ascii=False),
            'field_states_json':'{}','source_anchors_json':'[]','review_status':'pending_review',
            'business_importance':'normal','importance_rationale':'',
        }
        evidence=[{'excerpt':source,'accuracy_level':'exact'}]
        rechecked=_evaluate_review_eligibility(version,1,evidence)
        self.assertTrue(rechecked['can_confirm'])
        self.assertTrue(rechecked['can_batch_confirm'])
        still_doubt=dict(version,quality_flags_json=json.dumps(
            ['指标关联需核对：第1行的比较关系「不超过」未在证据摘录中直接对应'],ensure_ascii=False),
            metric_definition_json=json.dumps(metrics(row('其他项目','不超过','9','秒')),ensure_ascii=False))
        needs_review=_evaluate_review_eligibility(still_doubt,1,evidence)
        self.assertTrue(needs_review['can_confirm'])
        self.assertFalse(needs_review['can_batch_confirm'])
        integrity=dict(version,quality_flags_json=json.dumps(['伪造来源：不存在的段落'],ensure_ascii=False))
        self.assertFalse(_evaluate_review_eligibility(integrity,1,evidence)['can_confirm'])

    def test_method_manual_edits_clear_old_retry_failure_warning(self):
        from main import _evaluate_edited_draft_flags
        source='3. 查验操作：机电顾问团队对双回路电源自动切换装置（ATS）执行带载切换测试，切换时间必须严格控制在3秒以内。'
        payload={
            'statement':'机电顾问团队对ATS执行带载切换测试，切换时间不超过3秒。',
            'primary_category':'方法与工具', 'atom_type':'方法',
            'conditions':['供配电及变配电室验收时'],
            'actions':['机电顾问团队对ATS执行带载切换测试','确认切换时间严格控制在3秒以内'],
            'exceptions':[], 'metric_definition':None, 'case_details':None,
        }
        old=['结构化未完成：定向重抽仍不合格，请核对后人工整理',
             '结构化未完成：尚未核对并整理执行事项']
        evidence=[{'excerpt':source,'accuracy_level':'referenced'}]
        current=_evaluate_edited_draft_flags(payload,old,evidence)
        self.assertFalse(any('结构化未完成' in f for f in current), current)
        self.assertTrue(any('结构化未完成' in f for f in _evaluate_edited_draft_flags(
            {**payload,'actions':[]},old,evidence)))

    def test_duration_not_statistical_period(self):
        source='承压不低于0.8 MPa，并保持30分钟。'
        data,b=atom(source,primary_category='指标数据',atom_type='指标',
            metric_definition=metrics(row('压力','不低于','0.8','MPa'),
                                     row('持续时间','','30','分钟',period='30分钟')))
        self.assertTrue(any('统计口径需核对' in flag for flag in structural_quality_flags(data)))

    def test_quantitative_response_deadline_not_qualitative_note(self):
        from metric_semantics import parse_quantitative_note, normalize_generated_metric_rows
        source='针对室内跑水、总闸跳闸、电梯困人等一级紧急报修，维修技工到达现场时限不得超过15分钟。'
        row_data=row('维修技工到达现场时限','≤','15','分钟',
                     condition='室内跑水、总闸跳闸、电梯困人等一级紧急报修',note='不得超过15分钟')
        raw=metrics(row_data)
        self.assertEqual(parse_quantitative_note(row_data['note']),
                         {'relation':'≤','value':'15','unit':'分钟'})
        data, block_data=atom(source,primary_category='指标数据',atom_type='指标',metric_definition=raw)
        self.assertTrue(any('定性要求' in flag and '定量阈值' in flag
                            for flag in structural_quality_flags(data)))
        normalized=normalize_generated_metric_rows(raw,source)
        self.assertEqual(normalized['rows'][0]['note'],'')
        self.assertEqual(normalized['rows'][0]['relation'],'≤')
        self.assertEqual(normalized['rows'][0]['value'],'15')
        self.assertEqual(normalized['rows'][0]['unit'],'分钟')
        self.assertEqual(normalized['rows'][0]['condition'],row_data['condition'])
        self.assertEqual(raw['rows'][0]['note'],'不得超过15分钟')  # 旧数据不被修改
        self.assertEqual(structural_quality_flags({**data,'metric_definition':normalized}), [])
        from structured_quality import requires_targeted_retry
        self.assertFalse(requires_targeted_retry(structural_quality_flags(data)))
        generated=validate_and_sanitize_atoms([data],[block_data],'v1')[0]
        self.assertEqual(generated['metric_definition']['rows'][0]['note'],'')
        self.assertEqual(generated['source_evidence'][0]['excerpt'],source)

    def test_quantitative_note_mismatch_and_mixed_text_do_not_autorewrite(self):
        from metric_semantics import normalize_generated_metric_rows, parse_quantitative_note
        source='一级紧急报修维修技工到场时限不得超过15分钟，现场应穿着工服。'
        different=metrics(row('到达时限','≤','20','分钟',note='不得超过15分钟'))
        self.assertEqual(normalize_generated_metric_rows(different,source),different)
        data,_=atom(source,primary_category='指标数据',metric_definition=different)
        self.assertTrue(any('不一致' in flag for flag in structural_quality_flags(data)))
        mixed=metrics(row('到达时限','≤','15','分钟',note='不得超过15分钟，须穿着工服'))
        self.assertIsNone(parse_quantitative_note(mixed['rows'][0]['note']))
        self.assertEqual(normalize_generated_metric_rows(mixed,source),mixed)
        self.assertTrue(any('同时包含数值' in flag for flag in
                            structural_quality_flags({**data,'metric_definition':mixed})))
        unrelated=metrics(row('到达时限','≤','15','分钟',note='不得超过15分钟'))
        self.assertEqual(normalize_generated_metric_rows(unrelated,'维修技工须在20分钟内到场。'),
                         unrelated)

    def test_qualitative_and_legacy_metric(self):
        source='验收时设备标识应清晰完整。'
        data,b=atom(source,primary_category='指标数据', atom_type='指标',
            metric_definition=metrics(row('设备标识',note='清晰完整',condition='验收时')))
        self.assertEqual(structural_quality_flags(data),[])
        legacy,b=atom(source,primary_category='指标数据',atom_type='指标',
                      metric_definition={'name':'标识','unit':'','period':'','criteria':'清晰完整'})
        self.assertNotIn('指标堆积',' '.join(structural_quality_flags(legacy)))

    def test_missing_numeric_or_exception_requires_review(self):
        source='发现漏水时不得进入现场；压力不低于0.8 MPa并保持30分钟。'
        data,b=atom(source,primary_category='指标数据',atom_type='指标',
            metric_definition=metrics(row('压力','不低于','0.8','MPa')))
        flags=structural_quality_flags(data)
        self.assertTrue(any('30分钟' in flag for flag in flags))
        self.assertTrue(any('例外对应需核对' in flag for flag in flags))

    def test_value_and_unit_cross_pairing_doubt(self):
        source='压力要求不低于0.8 MPa，持续时间不少于30分钟。'
        data,b=atom(source,primary_category='指标数据',atom_type='指标',
          metric_definition=metrics(row('压力','不低于','30','MPa'),
                                    row('持续时间','不少于','0.8','分钟')))
        self.assertTrue(any('未在原文相邻出现' in x for x in structural_quality_flags(data)))

    def test_numeric_omission_in_non_metric_rule_requires_review(self):
        source='发现漏水时，值班人员须在15分钟内通知主管。'
        data,b=atom(source, statement='发现漏水时须通知主管',
            conditions=['发现漏水时'], actions=['值班人员通知主管'])
        flags=structural_quality_flags(data)
        self.assertTrue(any('15分钟' in flag for flag in flags))
        self.assertTrue(any('信息遗漏需核对' in flag for flag in flags))

    def test_no_automatic_targeted_retry_in_batch_extraction(self):
        text='发现管道漏水时，值班人员应关闭阀门并通知工程主管，无法接近时不得进入现场。'
        data,b=atom(text,statement=text,conditions=[text],actions=[text],exceptions=[text])
        calls=[]
        def fake(source_blocks, document_title, **kwargs):
            calls.append(kwargs.get('batch_context'))
            return [dict(data)], {'provider':'fake'}
        with patch('deepseek_extractor.extract_atoms_via_deepseek',side_effect=fake):
            candidates,context=extract_atoms_via_deepseek_batched([b],'模拟.md',api_key='fake-key')
        # 验证批次抽取不再自动发起二次定向重抽
        self.assertEqual(len(calls), 1)
        checked=validate_and_sanitize_atoms(candidates,[b],'v1')[0]
        self.assertTrue(any('结构化重复' in flag for flag in checked['quality_flags']))
        self.assertFalse(checked['issues_summary']['deterministic_errors'])
        self.assertTrue(checked['issues_summary']['model_doubts'])

    def test_confirmed_version_eligibility_and_blockers(self):
        from main import _evaluate_review_eligibility
        source = '工程部安防专员必须每两周进行一次角度校准与镜头擦拭。'
        version = {
            'title': '高空监控设备定期巡检要求 高空校准',
            'statement': '工程部安防专员必须每两周进行一次角度校准与镜头擦拭。',
            'primary_category': '制度与标准',
            'quality_flags_json': '[]',
            'field_states_json': '{}',
            'source_anchors_json': '[]',
            'review_status': 'confirmed',
            'business_importance': 'normal',
            'importance_rationale': '',
        }
        evidence = [{'excerpt': source, 'accuracy_level': 'exact'}]
        res = _evaluate_review_eligibility(version, 1, evidence)
        # 已确认版本的 confirmation_blockers 不应包含“该版本已确认或不处于待审核状态”伪阻断
        self.assertEqual(res['confirmation_blockers'], [])
        # 已确认版本不能再被重复审核确认，因此 can_confirm 为 False
        self.assertFalse(res['can_confirm'])
        self.assertFalse(res['can_batch_confirm'])
        self.assertIn('该版本已确认或不处于待审核状态', res['batch_review_reasons'])

if __name__=='__main__':
    unittest.main()

