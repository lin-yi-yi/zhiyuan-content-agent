import { useWorkspace } from '../components/WorkspaceContext';
import { ReactNode, useEffect, useRef, useState } from 'react';
import { api, request, Card, Draft, ReviewChecklistItem, Topic } from '../api/client';
import { exportCardsToPng, exportCardToPng, exportCardsZip } from '../utils/cardExport';
import { DraftNavigationTarget, readTargetDraft } from '../utils/draftNavigation';
import '../styles/editor-workspace.css';

const DRAFT_STATUS_LABELS:Record<string,string>={draft:'草稿 · 未经任务批准',pending:'待处理',awaiting_review:'待正式审核',approved:'正式审核通过',rejected:'正式审核退回',cancelled:'任务已取消',published:'已记录发布'};

const CARD_TYPE_LABELS: Record<string, string> = {
  cover: '封面', pain_point: '痛点', concept: '概念',
  workflow: '步骤', case: '案例', pitfall: '避坑', summary: '总结',
};

const LAYOUT_OPTIONS = [
  { value: 'problem_solution', label: '问题解决卡' },
  { value: 'clean_knowledge', label: '清爽知识卡' },
  { value: 'workflow_steps', label: '工作流步骤卡' },
  { value: 'case_note', label: '案例便签卡' },
  { value: 'risk_note', label: '避坑提醒卡' },
  { value: 'summary', label: '总结引导卡' },
  { value: 'tool_review', label: '工具测评卡' },
  { value: 'pitfall_opinion', label: '避坑观点卡' },
  { value: 'dev_log', label: '开发日志卡' },
];

const THEME_OPTIONS = [
  { value: 'lab_clean', label: '小红书清爽' },
  { value: 'workflow_blue', label: '流程蓝图' },
  { value: 'warm_note', label: '暖白笔记' },
  { value: 'deep_work', label: '深色专注' },
  { value: 'notebook', label: '笔记纸感' },
];

const CONTENT_TYPE_OPTIONS = [
  { value: '', label: '自动判断' },
  { value: 'github_project', label: 'GitHub 项目拆解' },
  { value: 'workflow_tutorial', label: 'AI 工作流教程' },
  { value: 'pitfall_guide', label: '避坑指南' },
  { value: 'tool_review', label: '工具测评' },
  { value: 'dev_log', label: '开发日志' },
  { value: 'case_study', label: '案例复盘' },
];

const TEMPLATE_OPTIONS = [
  { value: '', label: '跟随内容自动匹配' },
  { value: 'github_dark', label: 'GitHub 拆解风' },
  { value: 'workflow_clean', label: '流程卡风' },
  { value: 'pitfall_alert', label: '避坑警示风' },
  { value: 'tool_review_grid', label: '工具测评风' },
  { value: 'notebook_warm', label: '暖白笔记风' },
  { value: 'business_data', label: '商业数据风' },
];

const BODY_VARIANT_OPTIONS = [
  { value: 'first_person', label: 'A 第一人称' },
  { value: 'tutorial_steps', label: 'B 教程步骤' },
];

const CARD_FONT_SIZE_OPTIONS = [
  { value: 'micro', label: '极小' },
  { value: 'compact', label: '小号' },
  { value: 'default', label: '标准' },
  { value: 'large', label: '大号' },
];

const CARD_DENSITY_OPTIONS = [
  { value: 'max', label: '满版' },
  { value: 'dense', label: '高密度' },
  { value: 'compact', label: '紧凑' },
  { value: 'default', label: '标准' },
  { value: 'spacious', label: '舒适' },
];

const CARD_FONT_COLOR_OPTIONS = [
  { value: '', label: '跟随主题' },
  { value: '#171717', label: '深黑' },
  { value: '#172033', label: '墨蓝' },
  { value: '#231a16', label: '暖棕' },
  { value: '#ff365f', label: '品牌红' },
  { value: '#0f766e', label: '重点绿' },
  { value: '#ffffff', label: '高对比白' },
];

const BODY_FLOW_OPTIONS = [
  { value: 'points', label: '逐行要点' },
  { value: 'paragraphs', label: '段落正文' },
  { value: 'line_break', label: '保留换行' },
];

const COMPONENT_OPTIONS = [
  { value: 'hero_cover', label: '封面海报' },
  { value: 'icon_points', label: '图标要点' },
  { value: 'flow_steps', label: '流程箭头' },
  { value: 'code_block', label: '代码/Prompt' },
  { value: 'compare_table', label: '对比表格' },
  { value: 'checklist', label: '清单勾选' },
  { value: 'summary_cta', label: '总结 CTA' },
];

const CARD_TYPE_PRESETS = [
  {
    label: '满版讲述',
    layout_key: 'clean_knowledge',
    component_key: 'icon_points',
    style: { density: 'max', body_flow: 'paragraphs', font_size: 'micro', body_lines: 18, item_lines: 4, body_scale: 92, line_height: 128, show_highlight: false, show_footer: false },
  },
  {
    label: '步骤教程',
    layout_key: 'workflow_steps',
    component_key: 'flow_steps',
    style: { density: 'dense', body_flow: 'points', font_size: 'compact', body_lines: 8, item_lines: 3, body_scale: 96, line_height: 136, show_highlight: true, show_footer: true },
  },
  {
    label: '对比解释',
    layout_key: 'tool_review',
    component_key: 'compare_table',
    style: { density: 'dense', body_flow: 'points', font_size: 'compact', body_lines: 8, item_lines: 3, body_scale: 94, line_height: 132, show_highlight: true, show_footer: true },
  },
  {
    label: '避坑清单',
    layout_key: 'risk_note',
    component_key: 'checklist',
    style: { density: 'dense', body_flow: 'points', font_size: 'compact', body_lines: 9, item_lines: 3, body_scale: 94, line_height: 132, show_highlight: true, show_footer: true },
  },
  {
    label: '总结收束',
    layout_key: 'summary',
    component_key: 'summary_cta',
    style: { density: 'compact', body_flow: 'points', font_size: 'default', body_lines: 6, item_lines: 2, body_scale: 100, line_height: 140, show_highlight: true, show_footer: true },
  },
] as const;

const SYSTEM_COPY_LINES = new Set([
  '先让读者停下来',
  '先看它解决的真实问题',
  '这页负责封面钩子',
  '内容卡片',
  '从一个真实任务开始',
  '流程跑通比工具数量重要',
  '我会先避开这些坑',
  '少踩坑，比多试工具更重要',
  '先说适不适合你',
  '工具测评要回到场景',
  '记录一次真实开发思路',
  '开发日志要讲清取舍',
  '从一个小场景开始',
  '案例要能照着做',
]);

type CardFontSize = 'micro' | 'compact' | 'default' | 'large';
type CardDensity = 'max' | 'dense' | 'compact' | 'default' | 'spacious';
type CardBodyFlow = 'points' | 'paragraphs' | 'line_break';

type CardStyleOptions = {
  font_size: CardFontSize;
  density: CardDensity;
  font_color: string;
  body_flow: CardBodyFlow;
  title_scale: number;
  body_scale: number;
  line_height: number;
  body_lines: number;
  item_lines: number;
  show_highlight: boolean;
  show_footer: boolean;
};

function cleanReaderText(value?: string | null) {
  const text = (value || '').trim();
  return SYSTEM_COPY_LINES.has(text) ? '' : text;
}

function previewText(value?: string | null, maxLength = 1200) {
  const text = cleanReaderText(value);
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}\n...`;
}

function isCardFontSize(value: unknown): value is CardFontSize {
  return value === 'micro' || value === 'compact' || value === 'default' || value === 'large';
}

function isCardDensity(value: unknown): value is CardDensity {
  return value === 'max' || value === 'dense' || value === 'compact' || value === 'default' || value === 'spacious';
}

function isCardBodyFlow(value: unknown): value is CardBodyFlow {
  return value === 'points' || value === 'paragraphs' || value === 'line_break';
}

function isCardFontColor(value: unknown): value is string {
  return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value);
}

function clampNumber(value: unknown, fallback: number, min: number, max: number) {
  const num = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : Number.NaN;
  if (!Number.isFinite(num)) return fallback;
  return Math.max(min, Math.min(max, Math.round(num)));
}

function safeStyleJson(value?: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }
  return { ...(value as Record<string, unknown>) };
}

function parseCardStyle(card: Card): CardStyleOptions {
  const raw = safeStyleJson(card.style_json);
  const rawFontSize = (raw as { font_size?: unknown }).font_size;
  const rawDensity = (raw as { density?: unknown }).density;
  const rawFontColor = (raw as { font_color?: unknown }).font_color;
  const rawBodyFlow = (raw as { body_flow?: unknown }).body_flow;
  const rawTitleScale = (raw as { title_scale?: unknown }).title_scale;
  const rawBodyScale = (raw as { body_scale?: unknown }).body_scale;
  const rawLineHeight = (raw as { line_height?: unknown }).line_height;
  const rawBodyLines = (raw as { body_lines?: unknown }).body_lines;
  const rawItemLines = (raw as { item_lines?: unknown }).item_lines;
  const fontSize: CardFontSize = isCardFontSize(rawFontSize) ? rawFontSize : 'default';
  const density: CardDensity = isCardDensity(rawDensity) ? rawDensity : 'default';
  const fontColor = isCardFontColor(rawFontColor) ? rawFontColor : '';
  const bodyFlow = isCardBodyFlow(rawBodyFlow) ? rawBodyFlow : 'points';
  const showHighlight = (raw as { show_highlight?: unknown }).show_highlight;
  const showFooter = (raw as { show_footer?: unknown }).show_footer;

  return {
    font_size: fontSize,
    density,
    font_color: fontColor,
    body_flow: bodyFlow,
    title_scale: clampNumber(rawTitleScale, 100, 70, 140),
    body_scale: clampNumber(rawBodyScale, 100, 60, 140),
    line_height: clampNumber(rawLineHeight, 140, 105, 180),
    body_lines: clampNumber(rawBodyLines, getDensityLines(4, density), 1, 24),
    item_lines: clampNumber(rawItemLines, density === 'max' ? 4 : density === 'dense' ? 3 : 2, 1, 8),
    show_highlight: typeof showHighlight === 'boolean' ? showHighlight : true,
    show_footer: typeof showFooter === 'boolean' ? showFooter : true,
  };
}

function getDensityLines(capacity: number, density: CardDensity) {
  if (density === 'max') return Math.min(16, Math.max(6, capacity + 8));
  if (density === 'dense') return Math.min(10, Math.max(4, capacity + 4));
  if (density === 'compact') return Math.min(7, Math.max(3, capacity + 2));
  if (density === 'spacious') return Math.max(2, Math.min(capacity - 1, 2));
  return capacity;
}

function getCardDensityClass(density: CardDensity) {
  if (density === 'max') return 'card-density-max';
  if (density === 'dense') return 'card-density-dense';
  if (density === 'compact') return 'card-density-compact';
  if (density === 'spacious') return 'card-density-spacious';
  return '';
}

function getCardStyleVars(style: CardStyleOptions): Record<string, string> {
  const baseFontScale = style.font_size === 'micro' ? 0.82 : style.font_size === 'compact' ? 0.92 : style.font_size === 'large' ? 1.08 : 1;
  const densityFontScale = style.density === 'max' ? 0.9 : 1;
  const baseScale = baseFontScale * densityFontScale;
  const fontScale = String(Number(baseScale.toFixed(2)));
  const titleScale = String(Number((baseScale * (style.title_scale / 100)).toFixed(2)));
  const bodyScale = String(Number((baseScale * (style.body_scale / 100)).toFixed(2)));
  const densityScale = style.density === 'max' ? '0.58' : style.density === 'dense' ? '0.72' : style.density === 'compact' ? '0.86' : style.density === 'spacious' ? '1.1' : '1';
  const bodyLineClamp = String(style.body_lines || getDensityLines(4, style.density));
  const itemLineClamp = String(style.item_lines || (style.density === 'max' ? 4 : style.density === 'dense' ? 3 : 2));
  const bodyLineHeight = String(Number((style.line_height / 100).toFixed(2)));
  const vars: Record<string, string> = {
    '--card-font-scale': fontScale,
    '--card-title-font-scale': titleScale,
    '--card-body-font-scale': bodyScale,
    '--card-density-scale': densityScale,
    '--card-content-align': style.density === 'max' ? 'stretch' : style.density === 'dense' ? 'start' : 'center',
    '--card-item-line-clamp': itemLineClamp,
    '--card-body-line-clamp': bodyLineClamp,
    '--card-body-line-height': bodyLineHeight,
    '--card-cover-line-clamp': style.density === 'max' ? '6' : style.density === 'dense' ? '4' : style.density === 'compact' ? '3' : '2',
    '--card-summary-line-clamp': style.density === 'max' ? '10' : style.density === 'dense' ? '8' : style.density === 'compact' ? '6' : '5',
    '--card-show-footer': style.show_footer ? '1' : '0',
    '--card-show-highlight': style.show_highlight ? '1' : '0',
  };
  if (style.font_color) {
    vars['--card-text'] = style.font_color;
  }
  return vars;
}

function renderCardFrame(card: Card, options: { style?: CardStyleOptions; onClick?: () => void }) {
  const styleConfig = options.style || parseCardStyle(card);
  const className = [
    'xhs-card',
    `type-${card.card_type}`,
    `theme-${getThemeKey(card.theme_key)}`,
    `layout-${card.layout_key || 'clean_knowledge'}`,
    `component-${getComponentKey(card)}`,
    getCardDensityClass(styleConfig.density),
  ].join(' ');
  const componentKey = getComponentKey(card);
  const bodyContent = renderCardContent(card, styleConfig);

  return (
    <div
      className={className}
      onClick={options.onClick}
      role={options.onClick ? 'button' : undefined}
      tabIndex={options.onClick ? 0 : undefined}
      style={getCardStyleVars(styleConfig)}
    >
      <div className="xhs-card__hero">
        <div className="xhs-card__accent-bar" />
        <h4>{card.title}</h4>
        {cleanReaderText(card.subtitle) && <p className="xhs-card__subtitle">{cleanReaderText(card.subtitle)}</p>}
      </div>

      <div className="xhs-card__content-stack">
        {bodyContent}

        {componentKey !== 'summary_cta' && styleConfig.show_highlight && cleanReaderText(card.highlight) && (
          <div className="xhs-card__highlight">
            <strong>{cleanReaderText(card.highlight)}</strong>
          </div>
        )}
      </div>

      {styleConfig.show_footer && (
        <div className="xhs-card__footer">
          <span>{card.footer || '普通人的AI提效实验室'}</span>
        </div>
      )}
    </div>
  );
}

function renderSafeCardFrame(card: Card, options: { style?: CardStyleOptions; onClick?: () => void } = {}) {
  try {
    return renderCardFrame(card, options);
  } catch (error) {
    console.error('卡片预览渲染失败', error, card);
    return (
      <div className="xhs-card xhs-card-error" onClick={options.onClick} role={options.onClick ? 'button' : undefined}>
        <div>
          <strong>{card.title || `第 ${card.page_index} 页卡片`}</strong>
          <p>这张卡片的预览数据异常，仍可保存文字内容。</p>
        </div>
      </div>
    );
  }
}

function getBodyLines(body?: string | null) {
  return cleanReaderText(body)
    .split(/\n+/)
    .map(line => line.trim())
    .filter(line => line && !SYSTEM_COPY_LINES.has(line));
}

function getBodyBlocks(body: string | null | undefined, flow: CardBodyFlow) {
  const text = cleanReaderText(body).replace(/\r/g, '').trim();
  if (!text) return [];
  const blocks = flow === 'paragraphs'
    ? text.split(/\n{2,}/).map(block => block.replace(/\n+/g, ' ').trim())
    : text.split(/\n+/).map(line => line.trim());
  return blocks.filter(line => line && !SYSTEM_COPY_LINES.has(line));
}

function splitReadableUnits(value?: string | null) {
  return cleanReaderText(value)
    .replace(/\r/g, '')
    .replace(/([。！？!?；;])\s*/g, '$1\n')
    .split(/\n+/)
    .map(line => line.trim())
    .filter(line => line && !SYSTEM_COPY_LINES.has(line));
}

function groupUnitsAsParagraphs(units: string[], groupSize = 2) {
  const paragraphs: string[] = [];
  for (let index = 0; index < units.length; index += groupSize) {
    paragraphs.push(units.slice(index, index + groupSize).join(''));
  }
  return paragraphs.filter(Boolean);
}

function cleanStepLine(line: string) {
  return line.replace(/^\s*\d+[.、]\s*/, '');
}

function getPointIcon(card: Card, index: number) {
  if (card.card_type === 'pitfall') return '!';
  if (card.card_type === 'workflow') return '→';
  if (card.card_type === 'case') return '✓';
  if (card.card_type === 'concept') return '•';
  return String(index + 1);
}

function getThemeKey(theme?: string | null): string {
  return theme && THEME_OPTIONS.some(option => option.value === theme) ? theme : 'lab_clean';
}

function getThemeTextColor(theme?: string | null): string {
  const themeTextColors: Record<string, string> = {
    lab_clean: '#171717',
    workflow_blue: '#172033',
    warm_note: '#231a16',
    deep_work: '#f9fafb',
    notebook: '#1f2933',
  };
  return themeTextColors[getThemeKey(theme)] || '#171717';
}

function getComponentKey(card: Card): string {
  const value = safeStyleJson(card.style_json).component_key;
  return typeof value === 'string' && COMPONENT_OPTIONS.some(option => option.value === value) ? value : (
    card.card_type === 'cover' ? 'hero_cover'
      : card.card_type === 'summary' || card.layout_key === 'summary' ? 'summary_cta'
        : card.layout_key === 'workflow_steps' ? 'flow_steps'
          : 'icon_points'
  );
}

function getCardContentStats(card: Card, styleConfig: CardStyleOptions) {
  const componentKey = getComponentKey(card);
  const capacity = componentKey === 'hero_cover'
    ? Math.max(getDensityLines(2, styleConfig.density), styleConfig.body_lines || 0)
    : componentKey === 'summary_cta'
      ? 1
      : styleConfig.body_lines || getDensityLines(4, styleConfig.density);
  const blocks = styleConfig.body_flow !== 'points' && !['code_block', 'summary_cta', 'hero_cover'].includes(componentKey)
    ? getBodyBlocks(card.body, styleConfig.body_flow)
    : componentKey === 'compare_table'
      ? parseCompareRows(card.body).map(row => row.join(' | '))
      : getBodyLines(card.body);
  const charCount = cleanReaderText(card.body).length;
  return {
    charCount,
    blockCount: blocks.length,
    visibleBlocks: Math.min(blocks.length, capacity),
    hiddenBlocks: Math.max(0, blocks.length - capacity),
    capacity,
  };
}

function buildBodyReplacementSuggestions(card: Card, sourceText: string) {
  const units = splitReadableUnits(sourceText);
  if (units.length === 0) return [];
  const start = Math.max(0, Math.min(units.length - 1, (card.page_index - 2) * 2));
  const pageUnits = units.slice(start, start + 5);
  const nextUnits = units.slice(start + 2, start + 8);
  const compactUnits = pageUnits.length ? pageUnits : units.slice(0, 5);
  const suggestions = [
    {
      label: '换成段落讲述',
      body: groupUnitsAsParagraphs(compactUnits, 2).join('\n\n'),
      style: { density: 'max' as CardDensity, body_flow: 'paragraphs' as CardBodyFlow, font_size: 'micro' as CardFontSize, body_lines: 18, item_lines: 4, body_scale: 92, line_height: 128 },
    },
    {
      label: '换成要点列表',
      body: compactUnits.join('\n'),
      style: { density: 'dense' as CardDensity, body_flow: 'points' as CardBodyFlow, font_size: 'compact' as CardFontSize, body_lines: 8, item_lines: 3, body_scale: 96, line_height: 136 },
    },
    {
      label: '补充下一段',
      body: [cleanReaderText(card.body), ...nextUnits.slice(0, 3)].filter(Boolean).join('\n'),
      style: { density: 'max' as CardDensity, body_flow: 'line_break' as CardBodyFlow, font_size: 'micro' as CardFontSize, body_lines: 18, item_lines: 4, body_scale: 90, line_height: 126 },
    },
  ];
  return suggestions.filter(item => item.body.trim());
}

function firstOption(values?: string[] | null) {
  return Array.isArray(values) && values.length > 0 ? values[0] : '';
}

function textArray(values?: unknown): string[] {
  return Array.isArray(values)
    ? values.map(item => String(item || '').trim()).filter(Boolean)
    : [];
}

function bodyVariantMap(values?: unknown): Record<string, string> {
  if (!values || Array.isArray(values) || typeof values !== 'object') return {};
  return Object.entries(values as Record<string, unknown>).reduce<Record<string, string>>((acc, [key, value]) => {
    if (typeof value === 'string' && value.trim()) acc[key] = value;
    return acc;
  }, {});
}

function getBodyVariantLabel(key: string) {
  return BODY_VARIANT_OPTIONS.find(option => option.value === key)?.label || key;
}

function getDraftVersionLabel(item: Draft) {
  return item.variant_name || (item.selected_title ? `方案：${item.selected_title}` : `发布包 #${item.id}`);
}

function isVariantDraft(item: Draft) {
  return Boolean(item.variant_name || item.selected_title || item.content_type || item.template_key);
}

function getDraftVersionTypeLabel(item: Draft) {
  return isVariantDraft(item) ? '新方案' : '原始包';
}

function getDraftVersionMeta(item: Draft) {
  const parts = [
    `#${item.id}`,
    item.max_card_count ? `${item.max_card_count}页` : '',
    item.model_provider || 'unknown',
    item.model_name || 'unknown',
  ].filter(Boolean);
  return parts.join(' · ');
}

function getReviewProgress(items: ReviewChecklistItem[]) {
  const total = items.length;
  const checked = items.filter(item => item.checked).length;
  return { total, checked, ready: total > 0 && checked === total };
}

function cloneStyleJson(styleJson?: Record<string, unknown> | null) {
  const safe = safeStyleJson(styleJson);
  if (Object.keys(safe).length === 0) return null;
  return JSON.parse(JSON.stringify(safe)) as Record<string, unknown>;
}

function getOptionLabel(options: Array<{ value: string; label: string }>, value: string) {
  return options.find(option => option.value === value)?.label || '自动匹配';
}

function analyzeDraftCombination(params: {
  draft: Draft;
  cards: Card[];
  title: string;
  cover: string;
  bodyText: string;
  bodyVariantKey: string;
  contentType: string;
  templateKey: string;
  themeKey: string;
  maxCardCount: number;
}) {
  const issues: string[] = [];
  const suggestions: string[] = [];
  let score = 100;
  const titleLength = params.title.trim().length;
  const coverLength = params.cover.trim().length;
  const bodyLength = params.bodyText.trim().length;
  const tagCount = textArray(params.draft.hashtags).length;
  const baselineTitle = params.draft.selected_title || firstOption(textArray(params.draft.title_options));
  const baselineCover = params.draft.selected_cover_text || firstOption(textArray(params.draft.cover_text_options));
  const baselineBodyVariant = params.draft.body_variant_key || 'first_person';
  const baselineCardCount = params.draft.max_card_count || 7;
  const baselineContentType = params.draft.content_type || '';
  const baselineTemplate = params.draft.template_key || '';
  const baselineTheme = params.draft.theme_key || '';
  const selectedChanged = Boolean(
    params.title !== baselineTitle
    || params.cover !== baselineCover
    || params.bodyVariantKey !== baselineBodyVariant
    || params.maxCardCount !== baselineCardCount
    || params.contentType !== baselineContentType
    || params.templateKey !== baselineTemplate
    || params.themeKey !== baselineTheme
  );

  if (!params.title.trim()) {
    issues.push('标题缺失');
    score -= 18;
  } else if (titleLength < 10 || titleLength > 32) {
    issues.push('标题长度不够稳，建议 10-32 字');
    score -= 10;
  }
  if (!params.cover.trim()) {
    issues.push('封面文案缺失');
    score -= 15;
  } else if (coverLength > 20) {
    issues.push('封面文案偏长，建议压到 20 字以内');
    score -= 8;
  }
  if (bodyLength < 180) {
    issues.push('正文偏短，可信例子和操作细节可能不足');
    score -= 12;
  } else if (bodyLength > 1000) {
    issues.push('正文偏长，卡片和正文都可能显得拥挤');
    score -= 8;
  }
  if (tagCount < 3) {
    issues.push('标签不足 3 个');
    score -= 8;
  }
  if (params.cards.length > 0 && params.cards.length !== params.maxCardCount) {
    suggestions.push(`当前预览是 ${params.cards.length} 页，目标是 ${params.maxCardCount} 页，建议重新生成匹配卡片。`);
    score -= 5;
  }
  if (selectedChanged) {
    suggestions.push('组合已被修改，建议点击「生成匹配卡片」得到新版本。');
  }
  if (!params.templateKey) {
    suggestions.push('模板仍为自动匹配，想要更稳定的小红书风格可以指定一个模板。');
  }
  if (!params.themeKey) {
    suggestions.push('主题仍跟随模板，想统一视觉可手动选择主题。');
  }
  if (issues.length === 0) {
    suggestions.unshift('这个组合基础完整，可以生成新版本后再做质量评分。');
  }

  return {
    score: Math.max(0, Math.min(100, score)),
    issues,
    suggestions: suggestions.slice(0, 4),
    metrics: [
      { label: '标题', value: `${titleLength || 0}字` },
      { label: '封面', value: `${coverLength || 0}字` },
      { label: '正文', value: `${bodyLength || 0}字` },
      { label: '标签', value: `${tagCount}个` },
      { label: '页数', value: `${params.maxCardCount}页` },
      { label: '类型', value: getOptionLabel(CONTENT_TYPE_OPTIONS, params.contentType) },
      { label: '模板', value: getOptionLabel(TEMPLATE_OPTIONS, params.templateKey) },
      { label: '主题', value: getOptionLabel(THEME_OPTIONS, params.themeKey) },
    ],
  };
}

function parseCompareRows(body?: string | null) {
  return getBodyLines(body).map(line => {
    const parts = line.split('|').map(part => part.trim()).filter(Boolean);
    if (parts.length >= 2) return [parts[0], parts.slice(1).join(' | ')];
    return [line, ''];
  });
}

function getCoverFallback(card: Card) {
  const haystack = `${card.title || ''} ${card.subtitle || ''}`.toLowerCase();
  if (haystack.includes('坑') || haystack.includes('避坑')) return '先看哪里容易翻车，再看更稳的做法';
  if (haystack.includes('github') || haystack.includes('开源') || haystack.includes('langgraph')) return '从问题、适合谁、怎么复用三个角度拆开看';
  if (haystack.includes('工具') || haystack.includes('测评')) return '不看热度，先看它能不能解决你的任务';
  if (haystack.includes('日报') || haystack.includes('工作流') || haystack.includes('流程')) return '先跑通一个小流程，再考虑自动化升级';
  return '把问题拆开，给出能照着做的下一步';
}

function renderCardContent(card: Card, styleOptions?: CardStyleOptions): ReactNode {
  const styleConfig = styleOptions || parseCardStyle(card);
  const bodyLines = getBodyLines(card.body);
  const componentKey = getComponentKey(card);
  const density = styleConfig.body_lines || getDensityLines(4, styleConfig.density);
  const paragraphBlocks = getBodyBlocks(card.body, styleConfig.body_flow);

  if (styleConfig.body_flow !== 'points' && !['code_block', 'summary_cta', 'hero_cover'].includes(componentKey)) {
    const blocks = paragraphBlocks.length ? paragraphBlocks : [cleanReaderText(card.body) || ''];
    return (
      <div className={`xhs-card__paragraphs xhs-card__paragraphs--${styleConfig.body_flow}`}>
        {blocks.slice(0, density).map((line, index) => (
          <p key={`${card.id}-paragraph-${index}`}>{line}</p>
        ))}
      </div>
    );
  }

  if (componentKey === 'flow_steps') {
    return (
      <ol className="xhs-card__steps">
        {(bodyLines.length ? bodyLines : [card.body || '']).slice(0, density).map((line, index) => (
          <li key={`${card.id}-step-${index}`}>
            <span>{index + 1}</span>
            <p>{cleanStepLine(line)}</p>
          </li>
        ))}
      </ol>
    );
  }

  if (componentKey === 'code_block') {
    return (
      <div className="xhs-card__code">
        <div className="xhs-card__code-bar"><span /> <span /> <span /></div>
        <pre>{previewText(card.body) || '请把这个任务拆成输入、处理、输出、人工审核和复盘指标。'}</pre>
      </div>
    );
  }

  if (componentKey === 'compare_table') {
    const rows = parseCompareRows(card.body).slice(0, density);
    return (
      <div className="xhs-card__table">
        {rows.map((row, index) => (
          <div key={`${card.id}-row-${index}`} className="xhs-card__table-row">
            <span>{row[0]}</span>
            <strong>{row[1] || '更稳的做法'}</strong>
          </div>
        ))}
      </div>
    );
  }

  if (componentKey === 'checklist') {
    return (
      <ul className="xhs-card__checklist">
        {(bodyLines.length ? bodyLines : [card.body || '']).slice(0, density).map((line, index) => (
          <li key={`${card.id}-check-${index}`}>
            <span>✓</span>
            <p>{cleanStepLine(line)}</p>
          </li>
        ))}
      </ul>
    );
  }

  if (componentKey === 'summary_cta') {
    return (
      <div className="xhs-card__summary">
        <strong>{previewText(card.body, 260) || cleanReaderText(card.highlight) || '先把一个小任务做成流程，再把流程升级成 Agent。'}</strong>
        <span>{cleanReaderText(card.highlight) || '收藏后照着跑一遍'}</span>
      </div>
    );
  }

  if (componentKey === 'hero_cover') {
    const coverLines = bodyLines.length ? bodyLines : [getCoverFallback(card)];
    const coverLimit = Math.max(getDensityLines(2, styleConfig.density), styleConfig.body_lines || 0);
    return (
      <div className="xhs-card__cover-note">
        {coverLines.slice(0, coverLimit).map((line, index) => (
          <p key={`${card.id}-cover-${index}`}>{line}</p>
        ))}
      </div>
    );
  }

  return (
    <div className="xhs-card__points">
      {(bodyLines.length ? bodyLines : [card.body || '']).slice(0, density).map((line, index) => (
        <div key={`${card.id}-point-${index}`} className="xhs-card__point">
          <span>{getPointIcon(card, index)}</span>
          <p>{cleanStepLine(line)}</p>
        </div>
      ))}
    </div>
  );
}

export default function DraftEditorPage({target,onReturnToAgent,onNavigate}:{target?:DraftNavigationTarget|null;onReturnToAgent?:(runId:number)=>void;onNavigate?:(page:string)=>void}) {
  const {canWrite,canReview}=useWorkspace();
  const [editorTab, setEditorTab] = useState<'body'|'cards'|'review'>('body');
  const [libraryOpen, setLibraryOpen] = useState(!target);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [selectedTopic, setSelectedTopic] = useState<number | null>(null);
  const [draftVersions, setDraftVersions] = useState<Draft[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingCard, setEditingCard] = useState<Card | null>(null);
  const [evalResult, setEvalResult] = useState<any>(null);
  const [evalLoading, setEvalLoading] = useState(false);
  const [variantTitle, setVariantTitle] = useState('');
  const [variantCover, setVariantCover] = useState('');
  const [bodyVariantKey, setBodyVariantKey] = useState('first_person');
  const [bodyVariants, setBodyVariants] = useState<Record<string, string>>({});
  const [contentType, setContentType] = useState('');
  const [templateKey, setTemplateKey] = useState('');
  const [themeKey, setThemeKey] = useState('');
  const [maxCardCount, setMaxCardCount] = useState(7);
  const [variantLoading, setVariantLoading] = useState(false);
  const [variantMessage, setVariantMessage] = useState('');
  const [variantError,setVariantError]=useState('');
  const [reviewItems, setReviewItems] = useState<ReviewChecklistItem[]>([]);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [reviewMessage, setReviewMessage] = useState('');
  const [editingCardStyle, setEditingCardStyle] = useState<CardStyleOptions>(parseCardStyle({ id: 0, draft_id: 0, page_index: 0, card_type: '', title: '', subtitle: null, body: null, highlight: null, footer: null, layout_key: 'clean_knowledge', theme_key: 'lab_clean', style_json: {} } as Card));
  const [cardMutationKey, setCardMutationKey] = useState('');
  const [selectionError,setSelectionError]=useState('');
  const [exporting,setExporting]=useState(false);const [exportError,setExportError]=useState('');const [exportMessage,setExportMessage]=useState('');
  const [selectionMessage,setSelectionMessage]=useState('');
  const selectionGeneration=useRef(0);

  const loadExactDraft=async(draftId:number,topicId:number|null)=>{
    const generation=++selectionGeneration.current;
    setLoading(true);setSelectionError('');setSelectionMessage('');setSelectedTopic(topicId);
    setDraft(null);setCards([]);setDraftVersions([]);setEditingCard(null);
    try{
      const result=await readTargetDraft({draftId,topicId},{
        getDraft:id=>request<Draft>(`/api/drafts/${id}`),
        listCards:id=>api.listCardsByDraft(id),
        listVersions:id=>api.listDrafts({topic_id:id,limit:20}),
      });
      if(generation!==selectionGeneration.current)return;
      setDraft(result.draft);setSelectedTopic(result.draft.topic_id);setCards(result.cards);setDraftVersions(result.versions);
    }catch(e){if(generation===selectionGeneration.current)setSelectionError(`无法打开指定稿件 #${draftId}：${e instanceof Error?e.message:'读取失败'}。没有改为打开其它版本。`);}
    finally{if(generation===selectionGeneration.current)setLoading(false);}
  };

  useEffect(()=>{
    if(target)void loadExactDraft(target.draftId,target.topicId);
    return()=>{selectionGeneration.current+=1;};
  },[target?.draftId,target?.topicId,target?.runId]);

  const loadTopics = () => {
    api.listTopics({ limit: 50 }).then(d => setTopics(d.items)).catch(e => setSelectionError(`选题列表读取失败：${e instanceof Error?e.message:'请稍后重试'}`));
  };

  useEffect(() => { loadTopics(); }, []);

  useEffect(() => {
    if (!draft) return;
    setEditorTab('body');setLibraryOpen(false);
    setVariantTitle(draft.selected_title || firstOption(textArray(draft.title_options)) || '');
    setVariantCover(draft.selected_cover_text || firstOption(textArray(draft.cover_text_options)) || '');
    setBodyVariantKey(draft.body_variant_key === 'tutorial_steps' ? 'tutorial_steps' : 'first_person');
    setBodyVariants(bodyVariantMap(draft.body_variants));
    setContentType(draft.content_type || '');
    setTemplateKey(draft.template_key || '');
    setThemeKey(draft.theme_key || '');
    setMaxCardCount(draft.max_card_count || 7);
    setVariantMessage(draft.generated_reason || '');
    setVariantError('');
    setEvalResult(null);
    setReviewMessage('');
  }, [draft?.id]);

  useEffect(() => {
    if (!editingCard) {
      setEditingCardStyle(parseCardStyle({ id: 0, draft_id: 0, page_index: 0, card_type: '', title: '', subtitle: null, body: null, highlight: null, footer: null, layout_key: 'clean_knowledge', theme_key: 'lab_clean', style_json: {} } as Card));
      return;
    }
    setEditingCardStyle(parseCardStyle(editingCard));
  }, [editingCard?.id]);

  useEffect(() => {
    if (!editingCard) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const dialog = document.getElementById('card-edit-title')?.closest<HTMLElement>('[role="dialog"]');
    if (!dialog) return;
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary'))
      .filter(element => element.offsetParent !== null);
    focusable()[0]?.focus();
    const handleDialogKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setEditingCard(null); return; }
      if (event.key !== 'Tab') return;
      const controls = focusable();
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    dialog.addEventListener('keydown', handleDialogKey);
    return () => { dialog.removeEventListener('keydown', handleDialogKey); previousFocus?.focus(); };
  }, [editingCard?.id]);

  useEffect(() => {
    let active=true;
    if (!draft) {
      setReviewItems([]);
      return;
    }
    setReviewLoading(true);
    api.getReviewChecklist(draft.id)
      .then(items => {if(active)setReviewItems(items);})
      .catch(() => {if(active){setReviewItems([]);setReviewMessage('检查清单读取失败，请重新打开稿件后重试。');}})
      .finally(() => {if(active)setReviewLoading(false);});
    return()=>{active=false;};
  }, [draft?.id]);

  const handleGenerate = async (topicId: number) => {
    setLoading(true); setSelectedTopic(topicId);
    try {
      const d = await api.generateDraft(topicId);
      setDraft(d.draft); setCards(d.cards);
      setEditingCard(null);
      setDraftVersions(prev => [d.draft, ...prev.filter(item => item.id !== d.draft.id)]);
      loadTopics();
    } catch(e: any) { alert('生成失败: ' + e.message); }
    setLoading(false);
  };

  const handleSelectDraft = async (topicId: number) => {
    const generation=++selectionGeneration.current;
    setSelectedTopic(topicId);setLoading(true);setSelectionError('');setSelectionMessage('');
    setDraft(null);setCards([]);setDraftVersions([]);setEditingCard(null);
    try {
      const versions = await api.listDrafts({ topic_id: topicId, limit: 20 });
      const latestDraft = versions[0] || await api.getLatestDraftByTopic(topicId);
      if(generation!==selectionGeneration.current)return;
      await loadExactDraft(latestDraft.id,topicId);
    } catch(e) {
      if(generation===selectionGeneration.current){setLoading(false);setSelectionError(`无法读取该选题的稿件：${e instanceof Error?e.message:'读取失败'}`);}
    }
  };

  const handleEvaluate = async () => {
    if (!draft) return;
    setEvalLoading(true); setEvalResult(null);
    try {
      setEvalResult(await request(`/api/drafts/${draft.id}/evaluate`, { method: 'POST' }));
    } catch(e: any) { alert('评分失败: ' + e.message); }
    setEvalLoading(false);
  };

  const handleGenerateVariant = async () => {
    if (!draft) return;
    if(target?.draftId===draft.id){setVariantError('此稿件属于原内容任务，请直接编辑保存，再返回原任务重新提交；不能另建变体代替任务稿件。');return;}
    const selectedTitle = variantTitle || firstOption(textArray(draft.title_options));
    const selectedCover = variantCover || firstOption(textArray(draft.cover_text_options));
    if (!selectedTitle || !selectedCover) {
      alert('请先选择或填写标题和封面文案');
      return;
    }
    setVariantLoading(true);setVariantError('');
    try {
      const result = await api.generateDraftVariant(draft.id, {
        selected_title: selectedTitle,
        selected_cover_text: selectedCover,
        body_variant_key: bodyVariantKey,
        content_type: contentType,
        template_key: templateKey,
        theme_key: themeKey,
        max_card_count: maxCardCount,
        provider: draft.model_provider || 'local',
        model: draft.model_name || undefined,
        body_variants: bodyVariantMap(bodyVariants),
      });
      setDraft(result.draft);
      setCards(result.cards);
      setEditingCard(null);
      setDraftVersions(prev => [result.draft, ...prev.filter(item => item.id !== result.draft.id)]);
      setVariantMessage(result.variant.generated_reason);
      loadTopics();
    } catch(e: any) {
      setVariantError('未生成新版本：' + e.message);
    }
    setVariantLoading(false);
  };

  const handleSelectDraftVersion = async (selectedDraft: Draft) => {
    await loadExactDraft(selectedDraft.id,selectedDraft.topic_id);
  };

  const handleToggleReviewItem = (key: string, checked: boolean) => {
    setReviewItems(items => items.map(item => item.key === key ? { ...item, checked } : item));
    setReviewMessage('');
  };

  const handleReviewNoteChange = (key: string, note: string) => {
    setReviewItems(items => items.map(item => item.key === key ? { ...item, note } : item));
    setReviewMessage('');
  };

  const handleSaveReviewChecklist = async () => {
    if (!draft) return;
    setReviewSaving(true);
    try {
      const saved = await api.updateReviewChecklist(
        draft.id,
        reviewItems.map(item => ({ key: item.key, checked: item.checked, note: item.note || '' })),
      );
      setReviewItems(saved);
      setReviewMessage('检查清单已保存；正式审核状态未因此改变。');
    } catch (e: any) {
      alert('保存审核清单失败: ' + e.message);
    }
    setReviewSaving(false);
  };

  const refreshReviewState=async(draftId:number)=>{
    const generation=selectionGeneration.current;
    try{
      const [latest,checklist]=await Promise.all([request<Draft>(`/api/drafts/${draftId}`),api.getReviewChecklist(draftId)]);
      if(generation!==selectionGeneration.current)return;
      setDraft(current=>current?.id===draftId?{...current,status:latest.status}:current);
      setReviewItems(checklist);setReviewMessage('');
    }catch(e){if(generation===selectionGeneration.current)setSelectionError(`内容操作后未能刷新审核信息，请重新打开稿件核对：${e instanceof Error?e.message:'读取失败'}`);}
  };

  const syncCardsAfterMutation = (nextCards: Card[], nextEditingCardId?: number | null) => {
    setCards(nextCards);
    if(draft)void refreshReviewState(draft.id);
    if (nextEditingCardId === null) {
      setEditingCard(null);
      return;
    }
    const focusId = typeof nextEditingCardId === 'number' ? nextEditingCardId : editingCard?.id;
    if (focusId) {
      const nextEditing = nextCards.find(item => item.id === focusId) || null;
      setEditingCard(nextEditing);
      if (nextEditing) {
        setEditingCardStyle(parseCardStyle(nextEditing));
      }
    }
  };

  const runCardMutation = async <T,>(key: string, action: () => Promise<T>) => {
    setCardMutationKey(key);
    try {
      return await action();
    } finally {
      setCardMutationKey('');
    }
  };

  const saveCardDraft = async (card: Card) => {
    const saved = await api.updateCard(card.id, {
      title: card.title,
      subtitle: card.subtitle,
      body: card.body,
      highlight: card.highlight,
      footer: card.footer,
      layout_key: card.layout_key,
      theme_key: card.theme_key,
      style_json: safeStyleJson(card.style_json),
    });
    setCards(prev => prev.map(item => item.id === saved.id ? saved : item));
    setEditingCard(current => current?.id === saved.id ? saved : current);
    setEditingCardStyle(parseCardStyle(saved));
    await refreshReviewState(saved.draft_id);
    return saved;
  };

  const handleSaveCard = async (closeAfterSave = true) => {
    if (!editingCard) return null;
    try {
      const saved = await saveCardDraft(editingCard);
      if (closeAfterSave) {
        setEditingCard(null);
      }
      return saved;
    } catch (e: any) {
      alert('保存卡片失败: ' + e.message);
      return null;
    }
  };

  const handleSaveDraft = async () => {
    if (!draft) return;
    const generation=selectionGeneration.current;
    setLoading(true);setSelectionError('');setSelectionMessage('');
    try{
    const saved = await api.updateDraft(draft.id, {
      title_options: draft.title_options,
      cover_text_options: draft.cover_text_options,
      body_text: draft.body_text,
      hashtags: draft.hashtags,
      comment_guide: draft.comment_guide,
      fact_checks: draft.fact_checks,
      risk_tips: draft.risk_tips,
      aigc_notice: draft.aigc_notice,
      selected_title: variantTitle,
      selected_cover_text: variantCover,
      body_variant_key: bodyVariantKey,
      body_variants: bodyVariants,
      content_type: contentType,
      template_key: templateKey,
      theme_key: themeKey,
      max_card_count: maxCardCount,
      generated_reason: variantMessage,
    });
    if(generation!==selectionGeneration.current)return;
    setDraft(saved);
    await refreshReviewState(saved.id);
    if(generation===selectionGeneration.current)setSelectionMessage(`稿件 #${saved.id} 已保存。${saved.status==='rejected'?'请返回原任务重新提交审核。':saved.status==='awaiting_review'?'当前稿件等待正式审核。':'请继续检查正式审核状态。'}`);
    }catch(e){if(generation===selectionGeneration.current)setSelectionError(`保存未完成：${e instanceof Error?e.message:'请稍后重试'}`);}
    finally{if(generation===selectionGeneration.current)setLoading(false);}
  };

  const reviewProgress = getReviewProgress(reviewItems);
  const selectedBodyText = draft ? (bodyVariants[bodyVariantKey] || draft.body_text || '') : '';
  const comboAnalysis = draft ? analyzeDraftCombination({
    draft,
    cards,
    title: variantTitle || firstOption(textArray(draft.title_options)),
    cover: variantCover || firstOption(textArray(draft.cover_text_options)),
    bodyText: selectedBodyText,
    bodyVariantKey,
    contentType,
    templateKey,
    themeKey,
    maxCardCount,
  }) : null;

  const handleExportZip = async () => {
    if (!draft || exporting) return;
    setExporting(true);setExportError('');setExportMessage('');
    try { await exportCardsZip({
      cards,
      titleOptions: Array.from(new Set([variantTitle,...textArray(draft.title_options)].filter(Boolean))),
      bodyText: draft.body_text || undefined,
      hashtags: textArray(draft.hashtags),
      commentGuide: draft.comment_guide || undefined,
    }, draft.id);
    setExportMessage('内容包已交给浏览器下载，请查看下载列表。');
    } catch {setExportError('内容包未能生成。请先保存修改，再刷新页面重试；仍失败请检查浏览器是否允许下载。');}
    finally {setExporting(false);}
  };

  const handleBatchStyle = async (layoutKey: string, themeKey: string) => {
    if (!draft) return;
    await request(`/api/cards/draft/${draft.id}/batch-style`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ layout_key: layoutKey, theme_key: themeKey }) });
    syncCardsAfterMutation(await api.listCardsByDraft(draft.id));
  };

  const handleDeleteDraft = async () => {
    if (!draft) return;
    if (!confirm(`确定删除发布包 #${draft.id}？对应卡片也会一起删除。`)) return;
    await api.deleteDraft(draft.id);
    const nextVersions = draftVersions.filter(item => item.id !== draft.id);
    setDraftVersions(nextVersions);
    if (nextVersions.length > 0) {
      await handleSelectDraftVersion(nextVersions[0]);
    } else {
      setDraft(null);
      setCards([]);
      setEditingCard(null);
      loadTopics();
    }
  };

  const buildInsertedCardPayload = (afterCard?: Card | null): Partial<Card> => {
    const baseCard = afterCard || cards[cards.length - 1] || null;
    const insertAfterCover = afterCard?.card_type === 'cover';
    const nextPageIndex = afterCard ? afterCard.page_index + 1 : cards.length + 1;
    const nextStyleJson = cloneStyleJson(baseCard?.style_json);
    if (insertAfterCover && nextStyleJson) {
      delete nextStyleJson.component_key;
    }
    return {
      page_index: nextPageIndex,
      card_type: insertAfterCover ? 'concept' : baseCard?.card_type || 'concept',
      title: insertAfterCover ? '这一页开始讲清核心内容' : baseCard ? `${baseCard.title}（补充）` : '新的内容卡',
      subtitle: insertAfterCover ? '把关键概念、步骤或案例讲完整' : baseCard?.subtitle || '',
      body: insertAfterCover
        ? '先把这件事讲清楚\n再补一个具体步骤或例子\n让这一页成为真正有信息量的内容卡'
        : '补充这一页还没讲完的内容\n最好给出步骤、例子或对比\n尽量把卡面填满',
      highlight: insertAfterCover ? '一页只讲一个核心点，但把它讲透' : baseCard?.highlight || '',
      footer: baseCard?.footer || '普通人的AI提效实验室',
      layout_key: insertAfterCover ? 'clean_knowledge' : baseCard?.layout_key || 'clean_knowledge',
      theme_key: baseCard?.theme_key || draft?.theme_key || 'lab_clean',
      style_json: insertAfterCover
        ? { ...(nextStyleJson || {}), component_key: 'icon_points', body_flow: 'points' }
        : nextStyleJson,
    };
  };

  const handleCreateCard = async (afterCard?: Card | null) => {
    if (!draft) return;
    let sourceCard = afterCard || null;
    if (sourceCard && editingCard?.id === sourceCard.id) {
      const saved = await handleSaveCard(false);
      if (!saved) return;
      sourceCard = saved;
    }
    const payload = buildInsertedCardPayload(sourceCard);
    const actionKey = `create-${sourceCard?.id || 'tail'}`;
    try {
      const nextCards = await runCardMutation(actionKey, () => api.createCard(draft.id, payload));
      const inserted = nextCards.find(item => item.page_index === payload.page_index) || nextCards[nextCards.length - 1] || null;
      syncCardsAfterMutation(nextCards, inserted?.id ?? null);
    } catch (e: any) {
      alert('新增卡片失败: ' + e.message);
    }
  };

  const handleDuplicateCard = async (card: Card) => {
    let sourceCard = card;
    if (editingCard?.id === card.id) {
      const saved = await handleSaveCard(false);
      if (!saved) return;
      sourceCard = saved;
    }
    try {
      const nextCards = await runCardMutation(`duplicate-${sourceCard.id}`, () => api.duplicateCard(sourceCard.id));
      const duplicated = nextCards.find(item => item.id !== sourceCard.id && item.page_index === sourceCard.page_index + 1)
        || nextCards.find(item => item.title === `${sourceCard.title}（复制）`)
        || null;
      syncCardsAfterMutation(nextCards, duplicated?.id ?? sourceCard.id);
    } catch (e: any) {
      alert('复制卡片失败: ' + e.message);
    }
  };

  const handleSplitCard = async (card: Card) => {
    let sourceCard = card;
    if (editingCard?.id === card.id) {
      const saved = await handleSaveCard(false);
      if (!saved) return;
      sourceCard = saved;
    }
    try {
      const nextCards = await runCardMutation(`split-${sourceCard.id}`, () => api.splitCard(sourceCard.id));
      const continuation = nextCards.find(item => item.id !== sourceCard.id && item.page_index === sourceCard.page_index + 1) || null;
      syncCardsAfterMutation(nextCards, continuation?.id ?? sourceCard.id);
    } catch (e: any) {
      alert('拆分卡片失败: ' + e.message);
    }
  };

  const handleMoveCard = async (card: Card, direction: 'up' | 'down') => {
    let sourceCard = card;
    if (editingCard?.id === card.id) {
      const saved = await handleSaveCard(false);
      if (!saved) return;
      sourceCard = saved;
    }
    try {
      const nextCards = await runCardMutation(`move-${sourceCard.id}-${direction}`, () => api.moveCard(sourceCard.id, direction));
      syncCardsAfterMutation(nextCards, sourceCard.id);
    } catch (e: any) {
      alert(`${direction === 'up' ? '上移' : '下移'}卡片失败: ` + e.message);
    }
  };

  const handleDeleteCard = async (card: Card) => {
    if (!confirm(`确定删除第 ${card.page_index} 页「${card.title}」吗？`)) return;
    try {
      const nextCards = await runCardMutation(`delete-${card.id}`, () => api.deleteCard(card.id));
      const nextEditingId = editingCard?.id === card.id ? null : editingCard?.id;
      syncCardsAfterMutation(nextCards, nextEditingId);
    } catch (e: any) {
      alert('删除卡片失败: ' + e.message);
    }
  };

  const updateEditingCardStyle = (stylePatch: Partial<CardStyleOptions>, jsonPatch: Record<string, unknown>) => {
    if (!editingCard) return;
    const nextStyle = { ...editingCardStyle, ...stylePatch };
    const nextJson: Record<string, unknown> = safeStyleJson(editingCard.style_json);
    Object.entries(jsonPatch).forEach(([key, value]) => {
      if (value === '' || value === null || value === undefined) {
        delete nextJson[key];
      } else {
        nextJson[key] = value;
      }
    });
    setEditingCardStyle(nextStyle);
    setEditingCard({ ...editingCard, style_json: nextJson });
  };

  const setEditingCardBodyWithStyle = (
    body: string,
    stylePatch: Partial<CardStyleOptions> = {},
    jsonPatch: Record<string, unknown> = {},
  ) => {
    if (!editingCard) return;
    const nextStyle = { ...editingCardStyle, ...stylePatch };
    const nextJson: Record<string, unknown> = safeStyleJson(editingCard.style_json);
    Object.entries(jsonPatch).forEach(([key, value]) => {
      if (value === '' || value === null || value === undefined) {
        delete nextJson[key];
      } else {
        nextJson[key] = value;
      }
    });
    setEditingCardStyle(nextStyle);
    setEditingCard({ ...editingCard, body, style_json: nextJson });
  };

  const applyBodyTool = (mode: 'split' | 'paragraphs' | 'fill') => {
    if (!editingCard) return;
    const currentUnits = splitReadableUnits(editingCard.body);
    const sourceUnits = splitReadableUnits(selectedBodyText || draft?.body_text || '');
    if (mode === 'split') {
      setEditingCardBodyWithStyle(currentUnits.join('\n'), { density: 'dense', body_flow: 'line_break' }, { density: 'dense', body_flow: 'line_break' });
      return;
    }
    if (mode === 'paragraphs') {
      setEditingCardBodyWithStyle(
        groupUnitsAsParagraphs(currentUnits, 2).join('\n\n'),
        { density: 'max', body_flow: 'paragraphs', font_size: 'micro' },
        { density: 'max', body_flow: 'paragraphs', font_size: 'micro' },
      );
      return;
    }
    const combinedUnits = [...currentUnits];
    for (const unit of sourceUnits) {
      if (combinedUnits.length >= 8) break;
      if (!combinedUnits.some(existing => existing.includes(unit.slice(0, 16)) || unit.includes(existing.slice(0, 16)))) {
        combinedUnits.push(unit);
      }
    }
    setEditingCardBodyWithStyle(
      groupUnitsAsParagraphs(combinedUnits, 2).join('\n\n'),
      { density: 'max', body_flow: 'paragraphs', font_size: 'micro', show_highlight: false, show_footer: false },
      { density: 'max', body_flow: 'paragraphs', font_size: 'micro', show_highlight: false, show_footer: false },
    );
  };

  const applyCardTypePreset = (preset: typeof CARD_TYPE_PRESETS[number]) => {
    if (!editingCard) return;
    const nextStyle: CardStyleOptions = { ...editingCardStyle, ...preset.style };
    setEditingCardStyle(nextStyle);
    setEditingCard({
      ...editingCard,
      layout_key: preset.layout_key,
      style_json: {
        ...safeStyleJson(editingCard.style_json),
        ...preset.style,
        component_key: preset.component_key,
      },
    });
  };

  const editingContentStats = editingCard ? getCardContentStats(editingCard, editingCardStyle) : null;
  const fitEditingCardContent = () => {
    if (!editingCard || !editingContentStats) return;
    const blockCount = Math.max(1, editingContentStats.blockCount);
    const bodyScale = blockCount >= 14 ? 72 : blockCount >= 10 ? 80 : blockCount >= 7 ? 88 : 94;
    const lineHeight = blockCount >= 14 ? 110 : blockCount >= 10 ? 116 : blockCount >= 7 ? 122 : 128;
    const nextStyle: Partial<CardStyleOptions> = {
      density: 'max',
      font_size: 'micro',
      body_scale: bodyScale,
      line_height: lineHeight,
      body_lines: Math.min(24, Math.max(blockCount, editingCardStyle.body_lines)),
      item_lines: Math.min(8, Math.max(editingCardStyle.item_lines, blockCount >= 10 ? 4 : blockCount >= 7 ? 3 : 2)),
      show_highlight: blockCount <= 6 ? editingCardStyle.show_highlight : false,
      show_footer: blockCount <= 8 ? editingCardStyle.show_footer : false,
    };
    updateEditingCardStyle(nextStyle, nextStyle);
  };
  const replacementSuggestions = editingCard ? buildBodyReplacementSuggestions(editingCard, selectedBodyText || draft?.body_text || '') : [];
  const isMutatingCards = Boolean(cardMutationKey);

  return (
    <div className="editor-workspace">
      <header className="page-header editor-heading">
        <div><h1>稿件编辑</h1><p>先写清正文，再调整卡片，最后检查并导出。</p></div>
        <div className="editor-heading-actions">
          {target&&onReturnToAgent&&<button className="btn" disabled={loading||isMutatingCards} onClick={()=>onReturnToAgent(target.runId)}>返回审核任务 #{target.runId}</button>}
          {draft&&<button className="btn btn-primary" disabled={!canWrite||loading||isMutatingCards} onClick={handleSaveDraft}>{loading?'保存中…':'保存稿件'}</button>}
        </div>
      </header>
      {selectionError&&<div className="feedback error" role="alert">{selectionError}</div>}
      {selectionMessage&&<div className="feedback success" role="status">{selectionMessage}</div>}
      {loading&&!draft&&<div className="notice-strip" role="status">正在读取指定稿件与对应卡片…</div>}
      {target&&<div className="editor-task-context"><span>任务 #{target.runId} · 指定稿件 #{target.draftId}</span><span>{draft&&draft.id!==target.draftId?`当前查看 #${draft.id}，原任务仍关联 #${target.draftId}。`:'修改后先保存，再返回原任务提交审核。'}</span>{draft?.id!==target.draftId&&<button className="btn btn-sm" disabled={loading} onClick={()=>void loadExactDraft(target.draftId,target.topicId)}>打开指定稿件</button>}</div>}
      <details className="editor-disclosure editor-library" open={libraryOpen} onToggle={event=>setLibraryOpen(event.currentTarget.open)}>
        <summary><span>{draft?`切换稿件与版本 · 当前 #${draft.id}`:'选择已有稿件'}</span><span>{topics.length} 个选题</span></summary>
        <div className="editor-library-content">
          <h3 style={{ marginBottom: 12 }}>选题列表</h3>
          {topics.map(t => (
            <div key={t.id} style={{
              padding: '10px 12px', marginBottom: 6, borderRadius: 6, cursor: 'pointer',
              background: selectedTopic === t.id ? '#f0fdfa' : 'var(--bg)',
              border: selectedTopic === t.id ? '1px solid var(--accent)' : '1px solid transparent',
            }}>
              <button type="button" className="editor-topic-select" disabled={loading} onClick={() => handleSelectDraft(t.id)}>{t.title}</button>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span className={`score ${t.score >= 80 ? 'score-high' : 'score-mid'}`}>{t.score}分</span>
                <span className={`badge badge-${t.status}`}>{t.status}</span>
                <button className="btn btn-sm btn-primary" style={{ marginLeft: 'auto' }}
                        onClick={(e) => { e.stopPropagation(); handleGenerate(t.id); }}
                        disabled={!canWrite || (loading)}>
                  {loading && selectedTopic === t.id ? '生成中...' : '生成'}
                </button>
              </div>
            </div>
          ))}
          {topics.length === 0 && (
            <div className="empty">暂无选题，先创建内容任务。{onNavigate&&<button className="btn btn-sm" onClick={()=>onNavigate('agent')}>创建任务</button>}</div>
          )}
          {draftVersions.length > 0 && (
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
              <h3 style={{ marginBottom: 10 }}>稿件版本</h3>
              <div className="stack-list">
                {draftVersions.map(item => (
                  <button
                    key={item.id}
                    className={`list-button ${draft?.id === item.id ? 'active' : ''}`}
                    onClick={() => handleSelectDraftVersion(item)}
                  >
                    <div className="version-button__title">
                      <strong>{getDraftVersionLabel(item)}</strong>
                      <em>{draft?.id === item.id ? '当前' : getDraftVersionTypeLabel(item)}</em>
                    </div>
                    <span>{getDraftVersionMeta(item)}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </details>
      {draft ? <>
        <div className="editor-document-state"><span>稿件 #{draft.id}</span><strong>{DRAFT_STATUS_LABELS[draft.status]||'审核状态待确认'}</strong><span>{cards.length} 张卡片</span></div>
        <div className="editor-tabs" role="tablist" aria-label="稿件编辑步骤" onKeyDown={event=>{
          if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
          event.preventDefault();
          const tabs=Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
          const current=tabs.indexOf(document.activeElement as HTMLButtonElement);
          const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(current+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
          tabs[next]?.click();tabs[next]?.focus();
        }}>
          {([{key:'body',label:'正文'},{key:'cards',label:`卡片 · ${cards.length}`},{key:'review',label:'检查与导出'}] as const).map((tab,index)=><button key={tab.key} id={`editor-tab-${tab.key}`} role="tab" aria-selected={editorTab===tab.key} aria-controls={`editor-panel-${tab.key}`} tabIndex={editorTab===tab.key?0:-1} className={editorTab===tab.key?'active':''} onClick={()=>setEditorTab(tab.key)}><span>{index+1}</span>{tab.label}</button>)}
        </div>
        <section id="editor-panel-body" role="tabpanel" aria-labelledby="editor-tab-body" hidden={editorTab!=='body'} className="editor-tab-panel">
          <div className="editor-writing-sheet">
            <label className="editor-field">最终标题<input disabled={!canWrite} value={variantTitle} onChange={e=>setVariantTitle(e.target.value)} placeholder="写一个清楚、具体的标题" /></label>
            <label className="editor-field">封面短句<input disabled={!canWrite} value={variantCover} onChange={e=>setVariantCover(e.target.value)} placeholder="卡片封面的简短说明" /></label>
            <label className="editor-field editor-body-field"><span>正文 <small>{selectedBodyText.length} 字</small></span><textarea disabled={!canWrite} value={selectedBodyText} rows={16} onChange={e=>{const next={...bodyVariants,[bodyVariantKey]:e.target.value};setBodyVariants(next);setDraft({...draft,body_text:e.target.value,body_variant_key:bodyVariantKey,body_variants:next});}} placeholder="在这里完善正文，关键结论保留来源或引用。" /></label>
            <div className="editor-next-action"><span>正文与卡片分别保存，修改正文后请检查卡片是否同步。</span><button className="btn" onClick={()=>setEditorTab('cards')}>下一步：检查卡片 →</button></div>
          </div>
          <details className="editor-disclosure"><summary>标签与评论引导</summary><div className="editor-disclosure-content"><div style={{ marginBottom: 12 }}>
                  <strong style={{ fontSize: 12, color: 'var(--text-secondary)' }}>标签：</strong>
                  <input disabled={!canWrite}
                    value={textArray(draft.hashtags).join(' ')}
                    onChange={e => setDraft({ ...draft, hashtags: e.target.value.split(/\s+/).filter(Boolean) })}
                    style={{ marginTop: 6 }}
                  />
                </div>

                <div style={{ marginBottom: 12 }}>
                  <strong style={{ fontSize: 12, color: 'var(--text-secondary)' }}>评论引导：</strong>
                  <input disabled={!canWrite}
                    value={draft.comment_guide || ''}
                    onChange={e => setDraft({ ...draft, comment_guide: e.target.value })}
                    style={{ marginTop: 6 }}
                  />
                </div></div></details>
          <details className="editor-disclosure"><summary>标题候选、正文版本与生成新方案</summary><div className="editor-disclosure-content"><div className="variant-panel">
                <div className="variant-panel__header">
                  <div>
                    <h3>生成新发布方案</h3>
                    <p>{variantMessage || '选择标题、封面、正文版本和模板后生成新版本，不会覆盖当前方案。'}</p>
                    <p>{target?.draftId===draft.id?'当前是任务关联稿件，请直接保存修改并返回原任务处理审核，不另建变体。':`生成模型：${draft.model_provider||'本地规则（此稿未登记原模型）'}${draft.model_name?` · ${draft.model_name}`:''}。任务关联稿件不能另建变体，请回原任务编辑与重提。`}</p>
                  </div>
                  <button className="btn btn-primary" onClick={handleGenerateVariant} disabled={!canWrite || variantLoading || target?.draftId===draft.id}>
                    {variantLoading ? '生成中...' : '生成匹配卡片'}
                  </button>
                </div>
                {variantError&&<div className="feedback error" role="alert">{variantError}</div>}

                <div className="variant-grid">
                  <div className="variant-section">
                    <label>选择标题组合</label>
                    <div className="variant-choice-list">
                      {textArray(draft.title_options).map((title, index) => (
                        <button disabled={!canWrite}
                          key={`${title}-${index}`}
                          className={`variant-chip ${variantTitle === title ? 'active' : ''}`}
                          onClick={() => setVariantTitle(title)}
                        >
                          {title}
                        </button>
                      ))}
                    </div>
                    <input disabled={!canWrite}
                      value={variantTitle}
                      onChange={e => setVariantTitle(e.target.value)}
                      placeholder="也可以手动输入一个最终标题"
                    />
                  </div>

                  <div className="variant-section">
                    <label>选择封面组合</label>
                    <div className="variant-choice-list">
                      {textArray(draft.cover_text_options).map((cover, index) => (
                        <button disabled={!canWrite}
                          key={`${cover}-${index}`}
                          className={`variant-chip ${variantCover === cover ? 'active' : ''}`}
                          onClick={() => setVariantCover(cover)}
                        >
                          {cover}
                        </button>
                      ))}
                    </div>
                    <input disabled={!canWrite}
                      value={variantCover}
                      onChange={e => setVariantCover(e.target.value)}
                      placeholder="也可以手动输入封面短句"
                    />
                  </div>

                  <div className="variant-section">
                    <label>正文版本</label>
                    <div className="segmented-control">
                      {BODY_VARIANT_OPTIONS.map(option => (
                        <button disabled={!canWrite}
                          key={option.value}
                          className={bodyVariantKey === option.value ? 'active' : ''}
                          onClick={() => {
                            setBodyVariantKey(option.value);
                            const nextBody = bodyVariants[option.value];
                            if (nextBody) setDraft({ ...draft, body_text: nextBody, body_variant_key: option.value });
                          }}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                    <textarea disabled={!canWrite}
                      value={bodyVariants[bodyVariantKey] || draft.body_text || ''}
                      onChange={e => {
                        const next = { ...bodyVariants, [bodyVariantKey]: e.target.value };
                        setBodyVariants(next);
                        setDraft({ ...draft, body_text: e.target.value, body_variant_key: bodyVariantKey, body_variants: next });
                      }}
                      rows={5}
                      placeholder={`${getBodyVariantLabel(bodyVariantKey)}会在生成后自动补齐，也可以先手动改正文。`}
                    />
                  </div>

                  <div className="variant-section">
                    <label>卡片风格</label>
                    <div className="form-row">
                      <select disabled={!canWrite} value={contentType} onChange={e => setContentType(e.target.value)}>
                        {CONTENT_TYPE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                      <select disabled={!canWrite} value={templateKey} onChange={e => setTemplateKey(e.target.value)}>
                        {TEMPLATE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </div>
                    <div className="form-row">
                      <select disabled={!canWrite} value={themeKey} onChange={e => setThemeKey(e.target.value)}>
                        <option value="">模板默认主题</option>
                        {THEME_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                      <label className="variant-number-field">
                        <span>需要页数</span>
                        <input disabled={!canWrite}
                          type="number"
                          min={2}
                          max={7}
                          value={maxCardCount}
                          onChange={e => setMaxCardCount(Math.max(2, Math.min(Number(e.target.value) || 7, 7)))}
                        />
                      </label>
                    </div>
                  </div>
                </div>
              </div> <details className="draft-candidate-details">
                  <summary>候选库管理</summary>
                  <div className="draft-candidate-grid">
                    <div>
                      <strong>标题候选</strong>
                      {textArray(draft.title_options).map((t: string, i: number) => (
                        <input disabled={!canWrite}
                          key={i}
                          value={t}
                          onChange={e => {
                            const next = [...textArray(draft.title_options)];
                            const previous = next[i];
                            next[i] = e.target.value;
                            setDraft({ ...draft, title_options: next });
                            if (variantTitle === previous) setVariantTitle(e.target.value);
                          }}
                        />
                      ))}
                    </div>
                    <div>
                      <strong>封面文案</strong>
                      {textArray(draft.cover_text_options).map((t: string, i: number) => (
                        <input disabled={!canWrite}
                          key={i}
                          value={t}
                          onChange={e => {
                            const next = [...textArray(draft.cover_text_options)];
                            const previous = next[i];
                            next[i] = e.target.value;
                            setDraft({ ...draft, cover_text_options: next });
                            if (variantCover === previous) setVariantCover(e.target.value);
                          }}
                        />
                      ))}
                    </div>
                  </div>
                </details></div></details>
        </section>
        <section id="editor-panel-cards" role="tabpanel" aria-labelledby="editor-tab-cards" hidden={editorTab!=='cards'} className="editor-tab-panel">
          <div className="editor-card-intro"><p>点开卡片修改文字与样式。复制、拆分、移动等操作在每张卡片的“更多”中。</p><button className="btn" disabled={!canWrite||isMutatingCards} onClick={()=>handleCreateCard()}>新增卡片</button></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h3 style={{ margin: 0 }}>卡片预览（{cards.length} 页）</h3>
                {cards.length > 0 && (
                  <details className="editor-batch-options"><summary>批量版式与主题</summary><div className="batch-style-controls">
                    <span>批量应用:</span>
                    <select disabled={!canWrite} onChange={e => { if (e.target.value) handleBatchStyle(e.target.value, ''); e.target.value = ''; }}
                            style={{ padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12 }}>
                      <option value="">版式...</option>
                      {LAYOUT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <select disabled={!canWrite} onChange={e => { if (e.target.value) handleBatchStyle('', e.target.value); e.target.value = ''; }}
                            style={{ padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12 }}>
                      <option value="">主题...</option>
                      {THEME_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div></details>
                )}
              </div>
              <div className="card-preview-grid">
                {cards.map(card => (
                  <div key={card.id} className="card-preview-item">
                    <div className="card-preview-item__toolbar">
                      <span>第 {card.page_index} 页</span>
                      <div>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={e => {
                            e.stopPropagation();
                            setEditingCard(card);
                          }}
                        >
                          {canWrite?'编辑':'查看'}
                        </button>
                        <details className="editor-card-more"><summary>更多</summary><div className="editor-card-more__menu">
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={e => {
                            e.stopPropagation();
                            handleCreateCard(card);
                          }}
                          disabled={!canWrite || (isMutatingCards)}
                          title="在当前卡片后插入空白卡片"
                        >
                          +后插
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={e => {
                            e.stopPropagation();
                            handleDuplicateCard(card);
                          }}
                          disabled={!canWrite || (isMutatingCards)}
                          title="复制当前卡片"
                        >
                          复制
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={e => {
                            e.stopPropagation();
                            handleSplitCard(card);
                          }}
                          disabled={!canWrite || (isMutatingCards)}
                          title="将正文拆成两张卡"
                        >
                          拆分
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={e => {
                            e.stopPropagation();
                            handleMoveCard(card, 'up');
                          }}
                          disabled={!canWrite || (isMutatingCards)}
                          title="向上移动"
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={e => {
                            e.stopPropagation();
                            handleMoveCard(card, 'down');
                          }}
                          disabled={!canWrite || (isMutatingCards)}
                          title="向下移动"
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={e => {
                            e.stopPropagation();
                            handleDeleteCard(card);
                          }}
                          disabled={!canWrite || (isMutatingCards)}
                          title="删除当前卡片"
                        >
                          删除
                        </button>
                        </div></details>
                      </div>
                    </div>
                    {renderSafeCardFrame(card, {
                      style: parseCardStyle(card),
                      onClick: () => setEditingCard(card),
                    })}
                  </div>
                ))}
              </div>
          {cards.length===0&&<div className="editor-empty"><strong>还没有卡片</strong><p>可以新增空白卡片，也可以返回内容任务生成带卡片的稿件。</p></div>}
          <div className="editor-next-action"><button className="btn" onClick={()=>setEditorTab('body')}>← 返回正文</button><button className="btn btn-primary" onClick={()=>setEditorTab('review')}>下一步：检查与导出 →</button></div>
        </section>
        <section id="editor-panel-review" role="tabpanel" aria-labelledby="editor-tab-review" hidden={editorTab!=='review'} className="editor-tab-panel">
          <div className="editor-review-intro"><div><h3>检查内容，准备交付</h3><p>清单勾选和质量评分不会批准任务。正式通过或退回，以内容任务审核结果为准。</p></div><button className="btn" onClick={handleEvaluate} disabled={!canWrite||evalLoading}>{evalLoading?'检查中…':'运行质量检查'}</button></div>
          <details className="editor-disclosure"><summary>查看组合诊断与版式建议</summary>{comboAnalysis && (
                <div className="draft-combo-panel">
                  <div className="draft-combo-panel__header">
                    <div>
                      <h3>组合诊断</h3>
                      <p>根据当前标题、封面、正文、标签、页数和模板判断是否需要重新生成卡片。</p>
                    </div>
                    <div className={`draft-combo-score ${comboAnalysis.score >= 80 ? 'good' : comboAnalysis.score >= 60 ? 'warning' : 'danger'}`}>
                      <span>{comboAnalysis.score}</span>
                      <small>/100</small>
                    </div>
                  </div>

                  <div className="draft-combo-metrics">
                    {comboAnalysis.metrics.map(item => (
                      <div key={item.label} className="draft-combo-metric">
                        <span>{item.label}</span>
                        <strong>{item.value}</strong>
                      </div>
                    ))}
                  </div>

                  <div className="draft-combo-feedback">
                    <div className="draft-combo-list draft-combo-list--issues">
                      <h4>需要注意</h4>
                      {comboAnalysis.issues.length > 0 ? (
                        <ul>
                          {comboAnalysis.issues.map((issue, index) => <li key={`${issue}-${index}`}>{issue}</li>)}
                        </ul>
                      ) : (
                        <p>暂无明显结构问题。</p>
                      )}
                    </div>
                    <div className="draft-combo-list draft-combo-list--suggestions">
                      <h4>下一步建议</h4>
                      <ul>
                        {comboAnalysis.suggestions.map((suggestion, index) => <li key={`${suggestion}-${index}`}>{suggestion}</li>)}
                      </ul>
                    </div>
                  </div>
                </div>
              )}</details>
          <div className="editor-review-content">{textArray(draft.fact_checks).length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <strong style={{ fontSize: 12, color: 'var(--text-secondary)' }}>事实核验点：</strong>
                    <ul style={{ paddingLeft: 18, marginTop: 4, fontSize: 12, color: 'var(--text-secondary)' }}>
                      {textArray(draft.fact_checks).map((item, i) => <li key={i}>{item}</li>)}
                    </ul>
                  </div>
                )}

                {textArray(draft.risk_tips).length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <strong style={{ fontSize: 12, color: 'var(--text-secondary)' }}>模型检查建议（需人工核对）：</strong>
                    <ul style={{ paddingLeft: 18, marginTop: 4, fontSize: 12, color: 'var(--text-secondary)' }}>
                      {textArray(draft.risk_tips).map((item, i) => <li key={i}>{item}</li>)}
                    </ul>
                  </div>
                )}

                {draft.aigc_notice && (
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8 }}>
                    {draft.aigc_notice}
                  </div>
                )}

                <div className="review-checklist-panel">
                  <div className="review-checklist-panel__header">
                    <div>
                      <h4>发布前检查清单</h4>
                      <p>
                        {reviewLoading
                          ? '正在读取审核清单...'
                          : reviewItems.length > 0
                            ? `已完成 ${reviewProgress.checked}/${reviewProgress.total} 项`
                            : '暂无审核项'}
                      </p>
                    </div>
                    <span className={`review-status ${reviewProgress.ready ? 'ready' : reviewProgress.checked > 0 ? 'progress' : ''}`}>
                      {reviewProgress.ready ? '清单已完成' : reviewProgress.checked > 0 ? '清单进行中' : '清单未完成'}
                    </span>
                  </div>
                  {reviewItems.length > 0 && (
                    <div className="review-checklist">
                      {reviewItems.map(item => (
                        <label key={item.key} className={`review-checklist__item ${item.checked ? 'checked' : ''}`}>
                          <input disabled={!canReview}
                            type="checkbox"
                            checked={item.checked}
                            onChange={e => handleToggleReviewItem(item.key, e.target.checked)}
                          />
                          <div>
                            <strong>{item.label}</strong>
                            <input disabled={!canReview}
                              value={item.note || ''}
                              onChange={e => handleReviewNoteChange(item.key, e.target.value)}
                              placeholder="可选备注"
                            />
                          </div>
                        </label>
                      ))}
                    </div>
                  )}
                  <div className="review-checklist-panel__footer">
                    <button className="btn btn-sm btn-primary" onClick={handleSaveReviewChecklist} disabled={!canReview || (reviewSaving || reviewLoading || reviewItems.length === 0)}>
                      {reviewSaving ? '保存中...' : '保存检查清单'}
                    </button>
                    {reviewMessage && <span>{reviewMessage}</span>}
                  </div>
                </div>

                {/* 质量评分结果 */}
                {evalResult && (
                  <div style={{ marginTop: 16, padding: 16, background: '#fffbeb', borderRadius: 8, border: '1px solid #fcd34d' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                      <h4 style={{ margin: 0 }}>📊 质量评分</h4>
                      <span style={{
                        fontSize: 20, fontWeight: 700,
                        color: evalResult.overall_score >= 75 ? 'var(--green)' : evalResult.overall_score >= 50 ? '#d97706' : 'var(--red)',
                      }}>{evalResult.overall_score}/100</span>
                    </div>

                    <div style={{
                      padding: '4px 10px', borderRadius: 12, display: 'inline-block', fontSize: 12, fontWeight: 600, marginBottom: 12,
                      background: evalResult.publish_readiness === 'ready' ? '#d1fae5' : evalResult.publish_readiness === 'needs_review' ? '#fef3c7' : '#fee2e2',
                      color: evalResult.publish_readiness === 'ready' ? '#065f46' : evalResult.publish_readiness === 'needs_review' ? '#92400e' : '#991b1b',
                    }}>
                      {evalResult.publish_readiness === 'ready' ? '结构评分建议：可进入人工审核' : evalResult.publish_readiness === 'needs_review' ? '结构评分建议：需修改' : '结构评分建议：暂不采用'}
                    </div>

                    {/* 维度分数 */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 12 }}>
                      {evalResult.scores && Object.entries(evalResult.scores).map(([k, v]: [string, any]) => {
                        const labels: Record<string, string> = {
                          title_hook: '封面钩子', xiaohongshu_fit: '小红书适配', collectability: '收藏价值',
                          clarity: '可理解度', workflow_usability: '工作流实用', card_rhythm: '卡片节奏',
                          factual_risk: '合规', comment_guide: '评论引导', aigc_readiness: 'AIGC准备',
                        };
                        return (
                          <div key={k} style={{ fontSize: 12 }}>
                            <span style={{ color: 'var(--text-secondary)' }}>{labels[k] || k}</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                              <div style={{ flex: 1, height: 4, background: '#e5e7eb', borderRadius: 2 }}>
                                <div style={{ width: `${(Number(v) / 15) * 100}%`, height: '100%', background: Number(v) >= 10 ? 'var(--green)' : Number(v) >= 6 ? '#f59e0b' : 'var(--red)', borderRadius: 2 }} />
                              </div>
                              <span style={{ fontWeight: 600, minWidth: 20 }}>{v}</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    {/* 优点 */}
                    {evalResult.strengths?.length > 0 && (
                      <div style={{ marginBottom: 8 }}>
                        <strong style={{ fontSize: 12, color: 'var(--green)' }}>✅ 优点</strong>
                        {evalResult.strengths.map((s: string, i: number) => (
                          <div key={i} style={{ fontSize: 12, paddingLeft: 8, color: 'var(--text-secondary)' }}>• {s}</div>
                        ))}
                      </div>
                    )}

                    {/* 问题 */}
                    {evalResult.issues?.length > 0 && (
                      <div style={{ marginBottom: 8 }}>
                        <strong style={{ fontSize: 12, color: 'var(--red)' }}>⚠️ 问题</strong>
                        {evalResult.issues.map((issue: any, i: number) => (
                          <div key={i} style={{
                            fontSize: 12, padding: '4px 8px', margin: '2px 0', borderRadius: 4,
                            background: issue.level === 'high' ? '#fee2e2' : issue.level === 'medium' ? '#fef3c7' : '#f3f4f6',
                          }}>
                            {issue.card_page ? `[第${issue.card_page}页] ` : ''}{issue.message}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 修改建议 */}
                    {evalResult.rewrite_suggestions?.length > 0 && (
                      <div>
                        <strong style={{ fontSize: 12, color: 'var(--accent)' }}>💡 修改建议</strong>
                        {evalResult.rewrite_suggestions.map((s: string, i: number) => (
                          <div key={i} style={{ fontSize: 12, paddingLeft: 8, color: 'var(--text-secondary)' }}>• {s}</div>
                        ))}
                      </div>
                    )}

                    {evalResult._fallback && (
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8, fontStyle: 'italic' }}>
                        {evalResult._fallback}
                      </div>
                    )}
                  </div>
                )}</div>
          <div className="editor-export-panel"><div><h3>导出内容包</h3><p>下载到本机，用于预览或人工交付；不会向外部平台发布。</p></div><div className="editor-export-actions"><button className="btn" onClick={()=>exportCardsToPng(cards)} disabled={cards.length===0}>导出全部 PNG</button><button className="btn btn-primary" onClick={handleExportZip} disabled={cards.length===0||exporting}>{exporting?'正在生成内容包…':'下载 ZIP 内容包'}</button></div></div>{exportError&&<div className="feedback error" role="alert">{exportError}</div>}{exportMessage&&<div className="feedback success" role="status">{exportMessage}</div>}
          {onNavigate&&<button className="btn editor-evidence-link" onClick={()=>onNavigate('evidence')}>打开核验资料，检查事实来源</button>}
          <details className="editor-disclosure editor-danger"><summary>稿件管理</summary><div className="editor-disclosure-content"><p>删除会一并移除对应卡片，请确认当前稿件编号。</p><button disabled={!canWrite} className="btn btn-sm btn-danger" onClick={handleDeleteDraft}>删除稿件 #{draft.id}</button></div></details>
        </section>
      </> : !loading&&<div className="editor-empty"><strong>{target?'指定稿件尚未打开':'从一个稿件开始'}</strong><p>{target?'请检查上方提示后重新打开指定稿件，系统不会替换成其他版本。':'展开上方“选择已有稿件”，或创建一个新的内容任务。'}</p>{!target&&onNavigate&&<button className="btn btn-primary" onClick={()=>onNavigate('agent')}>创建内容任务</button>}</div>}

      {/* 卡片编辑 Modal */}
      {editingCard && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setEditingCard(null); }}>
          <div className="modal modal-wide editor-card-dialog" role="dialog" aria-modal="true" aria-labelledby="card-edit-title">
            <h2 id="card-edit-title">编辑卡片 #{editingCard.page_index} — {CARD_TYPE_LABELS[editingCard.card_type]}</h2>
            <div className="card-editor-layout">
              <div className="card-editor-form">
                <div className="form-group">
                  <label htmlFor="card-field-title">标题</label>
                  <input id="card-field-title" disabled={!canWrite} value={editingCard.title} onChange={e => setEditingCard({ ...editingCard, title: e.target.value })} />
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-subtitle">副标题</label>
                  <input id="card-field-subtitle" disabled={!canWrite} value={editingCard.subtitle || ''} onChange={e => setEditingCard({ ...editingCard, subtitle: e.target.value })} />
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-body">正文</label>
                  <textarea id="card-field-body" disabled={!canWrite} value={editingCard.body || ''} onChange={e => setEditingCard({ ...editingCard, body: e.target.value })} rows={8} />
                  {editingContentStats && (
                    <div className={`body-editor-meter ${editingContentStats.hiddenBlocks > 0 ? 'warning' : ''}`}>
                      <span>
                        {editingContentStats.charCount}字 · {editingContentStats.blockCount || 1}段/行 · 当前约显示 {editingContentStats.visibleBlocks || 1}/{Math.max(editingContentStats.blockCount, 1)}
                      </span>
                      {editingContentStats.hiddenBlocks > 0 && (
                        <div className="body-editor-meter__actions">
                          <strong>还有 {editingContentStats.hiddenBlocks} 段/行未进入卡面</strong>
                          <button disabled={!canWrite} type="button" onClick={fitEditingCardContent}>显示全部</button>
                        </div>
                      )}
                    </div>
                  )}
                  <details className="editor-disclosure"><summary>正文整理与替换建议</summary>
                  <div className="body-editor-tools">
                    <button disabled={!canWrite} type="button" className="btn btn-sm" onClick={() => applyBodyTool('split')}>按句拆行</button>
                    <button disabled={!canWrite} type="button" className="btn btn-sm" onClick={() => applyBodyTool('paragraphs')}>转段落正文</button>
                    <button disabled={!canWrite} type="button" className="btn btn-sm" onClick={() => applyBodyTool('fill')}>补满当前卡</button>
                  </div>
                  {replacementSuggestions.length > 0 && (
                    <div className="replacement-panel">
                      <strong>替换建议</strong>
                      <div>
                        {replacementSuggestions.map((item, index) => (
                          <button disabled={!canWrite}
                            key={`${item.label}-${index}`}
                            type="button"
                            onClick={() => setEditingCardBodyWithStyle(item.body, item.style, item.style)}
                          >
                            {item.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  </details>
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-highlight">强调句</label>
                  <input id="card-field-highlight" disabled={!canWrite} value={editingCard.highlight || ''} onChange={e => setEditingCard({ ...editingCard, highlight: e.target.value })} />
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-footer">页脚</label>
                  <input id="card-field-footer" disabled={!canWrite} value={editingCard.footer || ''} onChange={e => setEditingCard({ ...editingCard, footer: e.target.value })} />
                </div>
                <details className="editor-disclosure editor-card-style"><summary>模板、字体与高级排版</summary><div>
                <div className="form-group">
                  <label htmlFor="card-field-layout">卡片模板</label>
                  <select id="card-field-layout" disabled={!canWrite} value={editingCard.layout_key} onChange={e => setEditingCard({ ...editingCard, layout_key: e.target.value })}>
                    {LAYOUT_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-theme">主题风格</label>
                  <select id="card-field-theme" disabled={!canWrite} value={editingCard.theme_key} onChange={e => setEditingCard({ ...editingCard, theme_key: e.target.value })}>
                    {THEME_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-component">卡片组件</label>
                  <select id="card-field-component" disabled={!canWrite}
                    value={getComponentKey(editingCard)}
                    onChange={e => setEditingCard({
                      ...editingCard,
                      style_json: { ...safeStyleJson(editingCard.style_json), component_key: e.target.value },
                    })}
                  >
                    {COMPONENT_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                  <div className="card-type-presets">
                    {CARD_TYPE_PRESETS.map(preset => (
                      <button disabled={!canWrite} key={preset.label} type="button" onClick={() => applyCardTypePreset(preset)}>
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-font-size">字号级别</label>
                  <select id="card-field-font-size" disabled={!canWrite}
                    value={editingCardStyle.font_size}
                    onChange={e => {
                      const value = e.target.value as CardFontSize;
                      updateEditingCardStyle({ font_size: value }, { font_size: value });
                    }}
                  >
                    {CARD_FONT_SIZE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-font-color">字体颜色</label>
                  <div className="color-control">
                    <select id="card-field-font-color" disabled={!canWrite}
                      value={editingCardStyle.font_color}
                      onChange={e => {
                        const value = e.target.value;
                        updateEditingCardStyle({ font_color: value }, { font_color: value });
                      }}
                    >
                      {CARD_FONT_COLOR_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                    <input disabled={!canWrite}
                      type="color"
                      aria-label="自定义字体颜色"
                      value={editingCardStyle.font_color || getThemeTextColor(editingCard.theme_key)}
                      onChange={e => {
                        const value = e.target.value;
                        updateEditingCardStyle({ font_color: value }, { font_color: value });
                      }}
                    />
                  </div>
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-density">内容密度</label>
                  <select id="card-field-density" disabled={!canWrite}
                    value={editingCardStyle.density}
                    onChange={e => {
                      const value = e.target.value as CardDensity;
                      updateEditingCardStyle({ density: value }, { density: value });
                    }}
                  >
                    {CARD_DENSITY_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label htmlFor="card-field-body-flow">正文分段</label>
                  <select id="card-field-body-flow" disabled={!canWrite}
                    value={editingCardStyle.body_flow}
                    onChange={e => {
                      const value = e.target.value as CardBodyFlow;
                      updateEditingCardStyle({ body_flow: value }, { body_flow: value });
                    }}
                  >
                    {BODY_FLOW_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </div>
                <div className="advanced-style-panel">
                  <strong>高级字号与容量</strong>
                  <div className="advanced-style-grid">
                    <label>
                      <span>标题字号</span>
                      <input disabled={!canWrite}
                        type="range"
                        min="70"
                        max="140"
                        value={editingCardStyle.title_scale}
                        onChange={e => {
                          const value = Number(e.target.value);
                          updateEditingCardStyle({ title_scale: value }, { title_scale: value });
                        }}
                      />
                      <em>{editingCardStyle.title_scale}%</em>
                    </label>
                    <label>
                      <span>正文字号</span>
                      <input disabled={!canWrite}
                        type="range"
                        min="60"
                        max="140"
                        value={editingCardStyle.body_scale}
                        onChange={e => {
                          const value = Number(e.target.value);
                          updateEditingCardStyle({ body_scale: value }, { body_scale: value });
                        }}
                      />
                      <em>{editingCardStyle.body_scale}%</em>
                    </label>
                    <label>
                      <span>正文行高</span>
                      <input disabled={!canWrite}
                        type="range"
                        min="105"
                        max="180"
                        value={editingCardStyle.line_height}
                        onChange={e => {
                          const value = Number(e.target.value);
                          updateEditingCardStyle({ line_height: value }, { line_height: value });
                        }}
                      />
                      <em>{editingCardStyle.line_height}%</em>
                    </label>
                    <label>
                      <span>显示段/行</span>
                      <input disabled={!canWrite}
                        type="number"
                        min="1"
                        max="24"
                        value={editingCardStyle.body_lines}
                        onChange={e => {
                          const value = clampNumber(e.target.value, editingCardStyle.body_lines, 1, 24);
                          updateEditingCardStyle({ body_lines: value }, { body_lines: value });
                        }}
                      />
                    </label>
                    <label>
                      <span>单项行数</span>
                      <input disabled={!canWrite}
                        type="number"
                        min="1"
                        max="8"
                        value={editingCardStyle.item_lines}
                        onChange={e => {
                          const value = clampNumber(e.target.value, editingCardStyle.item_lines, 1, 8);
                          updateEditingCardStyle({ item_lines: value }, { item_lines: value });
                        }}
                      />
                    </label>
                  </div>
                </div>
                <label className="checkbox-row">
                  <input disabled={!canWrite}
                    type="checkbox"
                    checked={editingCardStyle.show_highlight}
                    onChange={e => {
                      const value = e.target.checked;
                      updateEditingCardStyle({ show_highlight: value }, { show_highlight: value });
                    }}
                  />
                  <span>显示强调块</span>
                </label>
                <label className="checkbox-row">
                  <input disabled={!canWrite}
                    type="checkbox"
                    checked={editingCardStyle.show_footer}
                    onChange={e => {
                      const value = e.target.checked;
                      updateEditingCardStyle({ show_footer: value }, { show_footer: value });
                    }}
                  />
                  <span>显示页脚</span>
                </label>
                </div></details>
              </div>
            <div className="card-editor-preview">
              <h3>实时预览</h3>
              <div className="card-editor-preview__canvas">
                {renderSafeCardFrame(editingCard, { style: editingCardStyle })}
              </div>
            </div>
          </div>
          <div className="form-actions">
            <details className="editor-card-actions"><summary>复制、拆分与调整顺序</summary><div className="card-edit-toolbar">
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => editingCard && handleDuplicateCard(editingCard)}
                disabled={!canWrite || (!editingCard || isMutatingCards)}
              >
                复制当前卡
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => editingCard && handleSplitCard(editingCard)}
                disabled={!canWrite || (!editingCard || isMutatingCards)}
              >
                拆分当前卡
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => editingCard && handleMoveCard(editingCard, 'up')}
                disabled={!canWrite || (!editingCard || isMutatingCards)}
              >
                上移
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => editingCard && handleMoveCard(editingCard, 'down')}
                disabled={!canWrite || (!editingCard || isMutatingCards)}
              >
                下移
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => editingCard && handleCreateCard(editingCard)}
                disabled={!canWrite || (!editingCard || isMutatingCards)}
              >
                在后插入新卡
              </button>
              <button
                type="button"
                className="btn btn-sm btn-danger"
                onClick={() => editingCard && handleDeleteCard(editingCard)}
                disabled={!canWrite || (!editingCard || isMutatingCards)}
              >
                删除当前卡
              </button>
            </div></details>
            <button className="btn" onClick={() => setEditingCard(null)}>取消</button>
            <button className="btn" onClick={() => exportCardToPng(editingCard, cards.length)}>导出 PNG</button>
            <button className="btn btn-primary" onClick={() => handleSaveCard()} disabled={!canWrite || (isMutatingCards)}>保存</button>
          </div>
        </div>
        </div>
      )}
    </div>
  );
}
