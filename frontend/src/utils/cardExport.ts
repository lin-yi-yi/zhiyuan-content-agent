import type { Card } from '../api/client';

const CARD_WIDTH = 900;
const CARD_HEIGHT = 1200;

type CardTheme = {
  bg: string;
  panel: string;
  text: string;
  muted: string;
  accent: string;
  accent2: string;
  soft: string;
  line: string;
};

const THEMES: Record<string, CardTheme> = {
  lab_clean: {
    bg: '#fffafa',
    panel: '#ffffff',
    text: '#171717',
    muted: '#6b6f76',
    accent: '#ff365f',
    accent2: '#0f9f8b',
    soft: '#fff0f3',
    line: '#f1d5dd',
  },
  workflow_blue: {
    bg: '#f7fbff',
    panel: '#ffffff',
    text: '#172033',
    muted: '#64748b',
    accent: '#2563eb',
    accent2: '#10b981',
    soft: '#eaf2ff',
    line: '#d7e5f6',
  },
  warm_note: {
    bg: '#fff9f2',
    panel: '#ffffff',
    text: '#231a16',
    muted: '#795c51',
    accent: '#f04438',
    accent2: '#f59e0b',
    soft: '#fff1df',
    line: '#efd5be',
  },
  deep_work: {
    bg: '#111827',
    panel: '#1f2937',
    text: '#f9fafb',
    muted: '#cbd5e1',
    accent: '#38bdf8',
    accent2: '#fb7185',
    soft: '#1f2937',
    line: '#334155',
  },
  notebook: {
    bg: '#fffdf7',
    panel: '#ffffff',
    text: '#1f2933',
    muted: '#6b7280',
    accent: '#2563eb',
    accent2: '#f97316',
    soft: '#eef4ff',
    line: '#e8dcc2',
  },
};

const COMPONENT_KEYS = [
  'hero_cover',
  'icon_points',
  'flow_steps',
  'code_block',
  'compare_table',
  'checklist',
  'summary_cta',
];

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
type CardStyleConfig = {
  density: CardDensity;
  fontScale: number;
  titleScale: number;
  bodyScale: number;
  lineHeightScale: number;
  densityScale: number;
  lineLimit: number;
  itemLineLimit: number;
  bodyFlow: CardBodyFlow;
  textColor: string;
  showHighlight: boolean;
  showFooter: boolean;
};

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

function parseCardStyleConfig(card: Card): CardStyleConfig {
  const raw = card.style_json || {};
  const fontSize = isCardFontSize((raw as { font_size?: unknown }).font_size)
    ? (raw as { font_size: CardFontSize }).font_size
    : 'default';
  const density = isCardDensity((raw as { density?: unknown }).density)
    ? (raw as { density: CardDensity }).density
    : 'default';
  const bodyFlow = isCardBodyFlow((raw as { body_flow?: unknown }).body_flow)
    ? (raw as { body_flow: CardBodyFlow }).body_flow
    : 'points';
  const textColor = isCardFontColor((raw as { font_color?: unknown }).font_color)
    ? (raw as { font_color: string }).font_color
    : '';
  const titleScale = clampNumber((raw as { title_scale?: unknown }).title_scale, 100, 70, 140) / 100;
  const bodyScale = clampNumber((raw as { body_scale?: unknown }).body_scale, 100, 60, 140) / 100;
  const lineHeightScale = clampNumber((raw as { line_height?: unknown }).line_height, 140, 105, 180) / 140;
  const defaultLineLimit = density === 'max' ? 12 : density === 'dense' ? 8 : density === 'compact' ? 6 : density === 'spacious' ? 3 : 4;
  const defaultItemLineLimit = density === 'max' ? 4 : density === 'dense' ? 3 : 2;
  const showHighlight = (raw as { show_highlight?: unknown }).show_highlight;
  const showFooter = (raw as { show_footer?: unknown }).show_footer;
  const baseFontScale = (fontSize === 'micro' ? 0.82 : fontSize === 'compact' ? 0.92 : fontSize === 'large' ? 1.08 : 1) * (density === 'max' ? 0.9 : 1);

  return {
    density,
    fontScale: baseFontScale,
    titleScale: baseFontScale * titleScale,
    bodyScale: baseFontScale * bodyScale,
    lineHeightScale,
    densityScale: density === 'max' ? 0.58 : density === 'dense' ? 0.72 : density === 'compact' ? 0.86 : density === 'spacious' ? 1.08 : 1,
    lineLimit: clampNumber((raw as { body_lines?: unknown }).body_lines, defaultLineLimit, 1, 24),
    itemLineLimit: clampNumber((raw as { item_lines?: unknown }).item_lines, defaultItemLineLimit, 1, 8),
    bodyFlow,
    textColor,
    showHighlight: typeof showHighlight === 'boolean' ? showHighlight : true,
    showFooter: typeof showFooter === 'boolean' ? showFooter : true,
  };
}

function cleanReaderText(value?: string | null) {
  const text = (value || '').trim();
  return SYSTEM_COPY_LINES.has(text) ? '' : text;
}

function renderCardToDataUrl(card: Card, totalPages = 7): string {
  const canvas = document.createElement('canvas');
  canvas.width = CARD_WIDTH;
  canvas.height = CARD_HEIGHT;
  const ctx = canvas.getContext('2d')!;
  const styleConfig = parseCardStyleConfig(card);
  const baseTheme = THEMES[card.theme_key] || THEMES.lab_clean;
  const theme = styleConfig.textColor ? { ...baseTheme, text: styleConfig.textColor } : baseTheme;
  drawCard(ctx, card, theme, totalPages, styleConfig);
  return canvas.toDataURL('image/png');
}

export function exportCardToPng(card: Card, totalPages = 7) {
  const dataUrl = renderCardToDataUrl(card, totalPages);
  if (dataUrl) download(dataUrl, `card-${String(card.page_index).padStart(2, "0")}-${safeName(card.title)}.png`);
}

export function exportCardsToPng(cards: Card[]) {
  cards.forEach((card, index) => {
    window.setTimeout(() => exportCardToPng(card, cards.length), index * 180);
  });
}

function drawCard(
  ctx: CanvasRenderingContext2D,
  card: Card,
  theme: CardTheme,
  totalPages: number,
  styleConfig: CardStyleConfig,
) {
  const isCover = card.card_type === 'cover';
  const componentKey = getComponentKey(card);

  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, CARD_WIDTH, CARD_HEIGHT);

  drawBackgroundPattern(ctx, theme);

  ctx.strokeStyle = theme.line;
  ctx.lineWidth = 3;
  roundRect(ctx, 38, 38, CARD_WIDTH - 76, CARD_HEIGHT - 76, 34);
  ctx.stroke();

  const gradient = ctx.createLinearGradient(78, 114, 186, 114);
  gradient.addColorStop(0, theme.accent);
  gradient.addColorStop(1, theme.accent2);
  ctx.fillStyle = gradient;
  roundRect(ctx, 78, 114, 112, 10, 5);
  ctx.fill();

  const titleY = isCover ? 188 : 174;
  const titleMaxLines = isCover ? 4 : 3;
  const titleStart = isCover ? 78 : 80;
  const titleFont = fitFontSize(
    ctx,
    card.title || '',
    CARD_WIDTH - 156,
    (isCover ? 82 : 64) * styleConfig.titleScale,
    Math.round((isCover ? 48 : 40) * styleConfig.titleScale),
  );
  ctx.fillStyle = theme.text;
  ctx.font = `900 ${titleFont}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
  const titleLines = drawWrappedText(ctx, card.title || '', titleStart, titleY, CARD_WIDTH - 156, Math.round(titleFont * 1.2), titleMaxLines);

  let cursorY = titleY + titleLines * Math.round(titleFont * 1.2) + 26;

  const subtitle = cleanReaderText(card.subtitle);
  if (subtitle) {
    ctx.fillStyle = theme.muted;
    const subtitleFont = fitFontSize(ctx, subtitle, CARD_WIDTH - 172, 36 * styleConfig.bodyScale, 25 * styleConfig.bodyScale);
    ctx.font = `700 ${subtitleFont}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    const subtitleLines = drawWrappedText(ctx, subtitle, 84, cursorY, CARD_WIDTH - 172, Math.round(subtitleFont * 1.4), 2);
    cursorY += subtitleLines * Math.round(subtitleFont * 1.38) + 20;
  }

  const highlight = componentKey === 'summary_cta' ? '' : cleanReaderText(card.highlight);
  const footerY = CARD_HEIGHT - 78;
  const contentAreaY = isCover ? Math.max(520, cursorY) : Math.max(360, cursorY);
  const contentAreaBottom = styleConfig.showFooter ? footerY - 72 : CARD_HEIGHT - 86;
  const contentAreaHeight = Math.max(150, contentAreaBottom - contentAreaY);
  const highlightVisible = styleConfig.showHighlight && Boolean(highlight);
  const highlightHeight = highlightVisible ? 112 : 0;
  const clusterGap = highlightVisible ? 24 : 0;
  const estimatedComponentHeight = estimateComponentHeight(ctx, card, componentKey, styleConfig, isCover);
  const clusterHeight = Math.min(
    contentAreaHeight,
    Math.max(estimatedComponentHeight + highlightHeight + clusterGap, 150),
  );
  const clusterOffsetRatio = styleConfig.density === 'max' ? 0 : styleConfig.density === 'dense' ? 0.08 : styleConfig.density === 'compact' ? 0.3 : 0.52;
  const clusterY = contentAreaY + Math.max(0, (contentAreaHeight - clusterHeight) * clusterOffsetRatio);
  const contentHeight = Math.max(110, clusterHeight - highlightHeight - clusterGap);
  const highlightY = clusterY + contentHeight + clusterGap;

  drawComponent(ctx, card, theme, componentKey, clusterY, contentHeight, isCover, styleConfig);

  if (highlightVisible) {
    const coverHighlight = componentKey === 'hero_cover';
    ctx.fillStyle = coverHighlight ? theme.accent : theme.soft;
    roundRect(ctx, 76, highlightY, CARD_WIDTH - 152, 112, 24);
    ctx.fill();
    ctx.strokeStyle = coverHighlight ? theme.accent : theme.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = coverHighlight ? theme.accent2 : theme.accent;
    roundRect(ctx, 76, highlightY, 10, 112, 5);
    ctx.fill();

    ctx.fillStyle = coverHighlight ? '#ffffff' : theme.text;
    const highlightFont = fitFontSize(ctx, highlight, CARD_WIDTH - 228, 32 * styleConfig.bodyScale, 24 * styleConfig.bodyScale);
    ctx.font = `900 ${highlightFont}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawWrappedText(ctx, highlight, 112, highlightY + 48, CARD_WIDTH - 228, Math.round(highlightFont * 1.38), 2);
  }

  if (styleConfig.showFooter) {
    ctx.fillStyle = theme.muted;
    ctx.font = `500 ${Math.round(24 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    ctx.fillText(card.footer || '普通人的AI提效实验室', 78, footerY);
  }
}

function estimateComponentHeight(ctx: CanvasRenderingContext2D, card: Card, componentKey: string, styleConfig: CardStyleConfig, isCover: boolean) {
  const lineCount = Math.max(1, getBodyLines(card.body).length);
  if (styleConfig.bodyFlow !== 'points' && !['code_block', 'summary_cta', 'hero_cover'].includes(componentKey)) {
    const blocks = getBodyBlocks(card.body, styleConfig.bodyFlow).slice(0, styleConfig.lineLimit);
    const rows = Math.max(1, blocks.length || lineCount);
    const minimumHeight = styleConfig.density === 'max'
      ? Math.min(680, rows * 48 + Math.max(0, rows - 1) * 7 + 42)
      : styleConfig.density === 'dense'
      ? Math.min(560, rows * 58 + Math.max(0, rows - 1) * 10 + 54)
      : rows * 78 + Math.max(0, rows - 1) * 16 + 64;
    // A paragraph may wrap onto many lines; match drawParagraphBody's font and
    // width instead of treating one source paragraph as one rendered row.
    const padding = Math.round(38 * styleConfig.densityScale);
    const fontSize = Math.round((styleConfig.density === 'max' ? 21 : styleConfig.density === 'dense' ? 22 : 25) * styleConfig.bodyScale);
    const lineHeight = Math.round(fontSize * (styleConfig.density === 'max' ? 1.28 : styleConfig.density === 'dense' ? 1.36 : 1.48) * styleConfig.lineHeightScale);
    const gap = Math.round((styleConfig.bodyFlow === 'line_break' ? 10 : 16) * styleConfig.densityScale);
    const width = CARD_WIDTH - 208 - padding;
    let wrappedLines = 0;
    ctx.save();
    ctx.font = `750 ${fontSize}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    for (const block of blocks) {
      let current = '';
      for (const char of cleanLine(block)) {
        if (current && ctx.measureText(current + char).width > width) {
          wrappedLines += 1;
          current = char;
        } else {
          current += char;
        }
      }
      if (current) wrappedLines += 1;
    }
    ctx.restore();
    return Math.max(minimumHeight, wrappedLines * lineHeight + padding * 2 + Math.max(0, blocks.length - 1) * gap);
  }
  if (componentKey === 'hero_cover') return isCover ? 190 : 170;
  if (componentKey === 'summary_cta') return 330;
  if (componentKey === 'code_block') return 260;
  if (componentKey === 'compare_table') {
    const rows = Math.min(styleConfig.lineLimit, Math.max(1, parseCompareRows(card.body).length));
    return rows * 82 + Math.max(0, rows - 1) * 14;
  }
  if (componentKey === 'flow_steps') {
    const rows = Math.min(styleConfig.lineLimit, lineCount);
    return rows * 78 + Math.max(0, rows - 1) * 18;
  }
  if (componentKey === 'checklist') {
    const rows = Math.min(styleConfig.lineLimit, lineCount);
    return rows * 62 + Math.max(0, rows - 1) * 12;
  }
  const rows = Math.min(styleConfig.lineLimit, lineCount);
  return rows === 1 ? 116 : rows * 76 + Math.max(0, rows - 1) * 16;
}

function drawComponent(
  ctx: CanvasRenderingContext2D,
  card: Card,
  theme: CardTheme,
  componentKey: string,
  y: number,
  height: number,
  isCover: boolean,
  styleConfig: CardStyleConfig,
) {
  if (styleConfig.bodyFlow !== 'points' && !['code_block', 'summary_cta', 'hero_cover'].includes(componentKey)) {
    drawParagraphBody(ctx, cleanReaderText(card.body) || '', theme, y, height, styleConfig);
    return;
  }
  if (componentKey === 'flow_steps') {
    drawWorkflowSteps(ctx, cleanReaderText(card.body) || '', theme, y, height, styleConfig);
    return;
  }
  if (componentKey === 'code_block') {
    drawCodeBlock(ctx, cleanReaderText(card.body) || '请把这个任务拆成输入、处理、输出、人工审核和复盘指标。', theme, y, height, styleConfig);
    return;
  }
  if (componentKey === 'compare_table') {
    drawCompareTable(ctx, cleanReaderText(card.body) || '', theme, y, height, styleConfig);
    return;
  }
  if (componentKey === 'checklist') {
    drawChecklist(ctx, cleanReaderText(card.body) || '', theme, y, height, styleConfig);
    return;
  }
  if (componentKey === 'summary_cta') {
    drawSummaryCta(ctx, card, theme, y, height, styleConfig);
    return;
  }
  if (componentKey === 'hero_cover') {
    drawCoverNote(ctx, cleanReaderText(card.body) || cleanReaderText(card.highlight) || getCoverFallback(card), theme, y, height, styleConfig);
    return;
  }
  drawIconPoints(ctx, card, theme, y, height, styleConfig);
}

function drawBodyPanel(
  ctx: CanvasRenderingContext2D,
  card: Card,
  theme: CardTheme,
  y: number,
  height: number,
  isCover: boolean,
) {
  if (!isCover) {
    ctx.fillStyle = theme.panel;
    roundRect(ctx, 76, y, CARD_WIDTH - 152, height, 24);
    ctx.fill();
    ctx.strokeStyle = theme.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    if (["problem_solution", "case_note", "risk_note", "pitfall_opinion"].includes(card.layout_key)) {
      ctx.fillStyle = theme.accent;
      roundRect(ctx, 76, y, 12, height, 6);
      ctx.fill();
    }
  }

  ctx.fillStyle = isCover ? theme.muted : theme.text;
  const bodyFont = fitFontSize(ctx, card.body || '', CARD_WIDTH - 204, isCover ? 38 : 34, isCover ? 28 : 24);
  ctx.font = `${isCover ? 700 : 600} ${bodyFont}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
  const lineHeight = Math.round(bodyFont * (isCover ? 1.55 : 1.58));
  const maxLines = Math.max(2, Math.floor((height - (isCover ? 0 : 48)) / lineHeight));
  drawWrappedText(ctx, card.body || '', isCover ? 86 : 112, y + (isCover ? 34 : 54), CARD_WIDTH - (isCover ? 172 : 224), lineHeight, maxLines);
}

function drawWorkflowSteps(
  ctx: CanvasRenderingContext2D,
  body: string,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  const steps = body
    .replace(/\r/g, '')
    .split('\n')
    .map(line => line.trim().replace(/^\d+[.、]\s*/, ''))
    .filter(Boolean)
    .slice(0, styleConfig.lineLimit);
  const visibleSteps = steps.length ? steps : [body.trim()];
  const gap = Math.round(18 * styleConfig.densityScale);
  const stepHeight = Math.min(92, Math.max(styleConfig.density === 'max' ? 40 : styleConfig.density === 'dense' ? 50 : 68, (height - gap * (visibleSteps.length - 1)) / visibleSteps.length));

  visibleSteps.forEach((step, index) => {
    const stepY = y + index * (stepHeight + gap);
    ctx.fillStyle = theme.panel;
    roundRect(ctx, 76, stepY, CARD_WIDTH - 152, stepHeight, 22);
    ctx.fill();
    ctx.strokeStyle = theme.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = theme.accent;
    ctx.beginPath();
    ctx.arc(126, stepY + stepHeight / 2, 25, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.font = `900 ${Math.round(26 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText(String(index + 1), 126, stepY + stepHeight / 2 + 9);
    ctx.textAlign = 'left';

    ctx.fillStyle = theme.text;
    ctx.font = `700 ${Math.round(27 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawWrappedText(ctx, step, 172, stepY + 40, CARD_WIDTH - 250, Math.round(36 * styleConfig.bodyScale * styleConfig.lineHeightScale), styleConfig.itemLineLimit);
  });
}

function drawParagraphBody(
  ctx: CanvasRenderingContext2D,
  body: string,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  const blocks = getBodyBlocks(body, styleConfig.bodyFlow);
  const visibleBlocks = blocks.length ? blocks.slice(0, styleConfig.lineLimit) : [body.trim()].filter(Boolean);
  if (!visibleBlocks.length) return;

  ctx.fillStyle = theme.panel;
  roundRect(ctx, 76, y, CARD_WIDTH - 152, height, 24);
  ctx.fill();
  ctx.strokeStyle = theme.line;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.fillStyle = theme.accent;
  roundRect(ctx, 76, y, 12, height, 6);
  ctx.fill();

  const paddingX = Math.round(38 * styleConfig.densityScale);
  const paddingY = Math.round(38 * styleConfig.densityScale);
  const fontSize = Math.round((styleConfig.density === 'max' ? 21 : styleConfig.density === 'dense' ? 22 : 25) * styleConfig.bodyScale);
  const lineHeight = Math.round(fontSize * (styleConfig.density === 'max' ? 1.28 : styleConfig.density === 'dense' ? 1.36 : 1.48) * styleConfig.lineHeightScale);
  const gap = Math.round((styleConfig.bodyFlow === 'line_break' ? 10 : 16) * styleConfig.densityScale);
  const maxLines = Math.max(3, Math.floor((height - paddingY * 2) / lineHeight));
  let usedLines = 0;
  let cursorY = y + paddingY + fontSize;

  ctx.fillStyle = theme.text;
  ctx.font = `750 ${fontSize}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;

  visibleBlocks.forEach((block, index) => {
    const remaining = maxLines - usedLines;
    if (remaining <= 0) return;
    const drawn = drawWrappedText(
      ctx,
      cleanLine(block),
      104,
      cursorY,
      CARD_WIDTH - 208 - paddingX,
      lineHeight,
      remaining,
    );
    usedLines += drawn;
    cursorY += drawn * lineHeight + (index === visibleBlocks.length - 1 ? 0 : gap);
  });
}

function drawIconPoints(
  ctx: CanvasRenderingContext2D,
  card: Card,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  const body = cleanReaderText(card.body) || '';
  const points = getBodyLines(body).slice(0, styleConfig.lineLimit);
  const visiblePoints = points.length ? points : [body.trim()].filter(Boolean);
  const gap = Math.round(16 * styleConfig.densityScale);
  const pointHeight = Math.min(82, Math.max(styleConfig.density === 'max' ? 36 : styleConfig.density === 'dense' ? 46 : 58, (height - gap * Math.max(0, visiblePoints.length - 1)) / Math.max(1, visiblePoints.length)));

  visiblePoints.forEach((point, index) => {
    const pointY = y + index * (pointHeight + gap);
    ctx.fillStyle = theme.panel;
    roundRect(ctx, 76, pointY, CARD_WIDTH - 152, pointHeight, 22);
    ctx.fill();
    ctx.strokeStyle = theme.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    const iconSize = styleConfig.density === 'max' ? 26 : 48;
    const iconX = 104;
    const iconY = pointY + Math.max(5, (pointHeight - iconSize) / 2);
    ctx.fillStyle = theme.soft;
    roundRect(ctx, iconX, iconY, iconSize, iconSize, styleConfig.density === 'max' ? 10 : 14);
    ctx.fill();
    ctx.fillStyle = theme.accent;
    ctx.font = `900 ${Math.round((styleConfig.density === 'max' ? 17 : 25) * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText(getPointGlyph(card.card_type, index), iconX + iconSize / 2, iconY + iconSize / 2 + Math.round(8 * styleConfig.bodyScale));
    ctx.textAlign = 'left';

    ctx.fillStyle = theme.text;
    ctx.font = `800 ${Math.round((styleConfig.density === 'max' ? 22 : 26) * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawWrappedText(ctx, cleanLine(point), 174, pointY + Math.min(36, pointHeight / 2 + 8), CARD_WIDTH - 252, Math.round((styleConfig.density === 'max' ? 28 : 34) * styleConfig.bodyScale * styleConfig.lineHeightScale), styleConfig.itemLineLimit);
  });
}

function getPointGlyph(cardType: string, index: number) {
  if (cardType === 'pitfall') return '!';
  if (cardType === 'workflow') return '→';
  if (cardType === 'case') return '✓';
  if (cardType === 'concept') return '•';
  return String(index + 1);
}

function drawCodeBlock(
  ctx: CanvasRenderingContext2D,
  body: string,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  ctx.fillStyle = theme.panel;
  roundRect(ctx, 76, y, CARD_WIDTH - 152, height, 24);
  ctx.fill();
  ctx.strokeStyle = theme.line;
  ctx.lineWidth = 2;
  ctx.stroke();

  for (let i = 0; i < 3; i += 1) {
    ctx.fillStyle = i === 0 ? theme.accent : i === 1 ? theme.accent2 : theme.line;
    ctx.beginPath();
    ctx.arc(114 + i * 28, y + 34, 8, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.fillStyle = theme.text;
  const codeFont = Math.round(22 * styleConfig.bodyScale);
  const codeLineHeight = Math.round(34 * styleConfig.bodyScale * styleConfig.lineHeightScale);
  ctx.font = `700 ${codeFont}px "SFMono-Regular", Consolas, "PingFang SC", monospace`;
  drawWrappedText(ctx, body, 112, y + 84, CARD_WIDTH - 224, codeLineHeight, Math.max(3, Math.floor((height - 112) / codeLineHeight)));
}

function drawCompareTable(
  ctx: CanvasRenderingContext2D,
  body: string,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  const rows = parseCompareRows(body).slice(0, styleConfig.lineLimit);
  const visibleRows = rows.length ? rows : [['直接发草稿', '人工核对事实']];
  const gap = Math.round(14 * styleConfig.densityScale);
  const rowHeight = Math.min(82, Math.max(styleConfig.density === 'max' ? 38 : styleConfig.density === 'dense' ? 48 : 60, (height - gap * (visibleRows.length - 1)) / visibleRows.length));

  visibleRows.forEach((row, index) => {
    const rowY = y + index * (rowHeight + gap);
    ctx.fillStyle = theme.panel;
    roundRect(ctx, 76, rowY, 340, rowHeight, 18);
    ctx.fill();
    ctx.strokeStyle = theme.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = theme.soft;
    roundRect(ctx, 436, rowY, CARD_WIDTH - 512, rowHeight, 18);
    ctx.fill();
    ctx.strokeStyle = theme.line;
    ctx.stroke();

    ctx.fillStyle = theme.muted;
    ctx.font = `750 ${Math.round(22 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawCellBadge(ctx, '原', 108, rowY + 36, theme.soft, theme.accent);
    ctx.fillStyle = theme.muted;
    ctx.font = `750 ${Math.round(22 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawWrappedText(ctx, row[0], 140, rowY + 36, 246, Math.round(29 * styleConfig.bodyScale * styleConfig.lineHeightScale), styleConfig.itemLineLimit);

    ctx.fillStyle = theme.text;
    ctx.font = `900 ${Math.round(23 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawCellBadge(ctx, '改', 468, rowY + 36, theme.accent, '#ffffff');
    ctx.fillStyle = theme.text;
    ctx.font = `900 ${Math.round(23 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawWrappedText(ctx, row[1] || '更稳的做法', 500, rowY + 36, CARD_WIDTH - 606, Math.round(30 * styleConfig.bodyScale * styleConfig.lineHeightScale), styleConfig.itemLineLimit);
  });
}

function drawChecklist(
  ctx: CanvasRenderingContext2D,
  body: string,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  const items = getBodyLines(body).slice(0, styleConfig.lineLimit);
  const visibleItems = items.length ? items : [body.trim()].filter(Boolean);
  const gap = Math.round(12 * styleConfig.densityScale);
  const itemHeight = Math.min(70, Math.max(styleConfig.density === 'max' ? 34 : styleConfig.density === 'dense' ? 40 : 48, (height - gap * Math.max(0, visibleItems.length - 1)) / Math.max(1, visibleItems.length)));

  visibleItems.forEach((item, index) => {
    const itemY = y + index * (itemHeight + gap);
    ctx.fillStyle = theme.panel;
    roundRect(ctx, 76, itemY, CARD_WIDTH - 152, itemHeight, 18);
    ctx.fill();
    ctx.strokeStyle = theme.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = theme.accent;
    ctx.beginPath();
    ctx.arc(120, itemY + itemHeight / 2, 18, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(110, itemY + itemHeight / 2);
    ctx.lineTo(118, itemY + itemHeight / 2 + 8);
    ctx.lineTo(132, itemY + itemHeight / 2 - 9);
    ctx.stroke();

    ctx.fillStyle = theme.text;
    ctx.font = `800 ${Math.round(24 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    drawWrappedText(ctx, cleanLine(item), 162, itemY + itemHeight / 2 + 8, CARD_WIDTH - 240, Math.round(30 * styleConfig.bodyScale * styleConfig.lineHeightScale), styleConfig.itemLineLimit);
  });
}

function drawSummaryCta(
  ctx: CanvasRenderingContext2D,
  card: Card,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  const panelHeight = Math.min(height, 360);
  const panelY = y + Math.max(0, (height - panelHeight) * 0.46);
  ctx.fillStyle = theme.panel;
  roundRect(ctx, 76, panelY, CARD_WIDTH - 152, panelHeight, 28);
  ctx.fill();
  ctx.strokeStyle = theme.line;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.fillStyle = theme.text;
  const body = cleanReaderText(card.body) || cleanReaderText(card.highlight) || '先把一个小任务做成流程，再把流程升级成 Agent。';
  const bodyFont = fitFontSize(ctx, body, CARD_WIDTH - 224, 36 * styleConfig.bodyScale, 27 * styleConfig.bodyScale);
  ctx.font = `900 ${bodyFont}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
  const bodyLines = drawWrappedText(ctx, body, 114, panelY + 78, CARD_WIDTH - 228, Math.round(bodyFont * 1.45 * styleConfig.lineHeightScale), styleConfig.density === 'max' ? 10 : styleConfig.density === 'dense' ? 8 : styleConfig.density === 'compact' ? 6 : 5);

  const ctaY = Math.min(panelY + panelHeight - 84, panelY + 102 + bodyLines * Math.round(bodyFont * 1.45));
  ctx.fillStyle = theme.accent;
  roundRect(ctx, 114, ctaY, CARD_WIDTH - 228, 54, 18);
  ctx.fill();
  ctx.fillStyle = '#ffffff';
  ctx.font = `900 ${Math.round(23 * styleConfig.bodyScale)}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
  drawWrappedText(ctx, cleanReaderText(card.highlight) || '收藏后照着跑一遍', 140, ctaY + 35, CARD_WIDTH - 280, Math.round(26 * styleConfig.bodyScale * styleConfig.lineHeightScale), 1);
}

function drawCoverNote(
  ctx: CanvasRenderingContext2D,
  body: string,
  theme: CardTheme,
  y: number,
  height: number,
  styleConfig: CardStyleConfig,
) {
  if (!body.trim()) return;
  const panelHeight = Math.min(230, Math.max(138, height * 0.46));
  ctx.fillStyle = theme.panel;
  roundRect(ctx, 76, y, CARD_WIDTH - 152, panelHeight, 24);
  ctx.fill();
  ctx.strokeStyle = theme.line;
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = theme.accent;
  roundRect(ctx, 76, y, 12, panelHeight, 6);
  ctx.fill();

  ctx.fillStyle = theme.text;
  const bodyFont = fitFontSize(ctx, body, CARD_WIDTH - 172, 36 * styleConfig.bodyScale, 27 * styleConfig.bodyScale);
  ctx.font = `800 ${bodyFont}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
  drawWrappedText(ctx, body, 112, y + 58, CARD_WIDTH - 224, Math.round(bodyFont * 1.5 * styleConfig.lineHeightScale), styleConfig.density === 'max' ? 6 : styleConfig.density === 'dense' ? 5 : styleConfig.density === 'compact' ? 4 : 3);
}

function drawCellBadge(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  bg: string,
  color: string,
) {
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.arc(x + 10, y - 8, 11, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.font = '900 12px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(text, x + 10, y - 4);
  ctx.textAlign = 'left';
}

function getBodyLines(body?: string | null) {
  return cleanReaderText(body)
    .replace(/\r/g, '')
    .split('\n')
    .map(line => line.trim())
    .filter(line => line && !SYSTEM_COPY_LINES.has(line));
}

function getBodyBlocks(body: string | null | undefined, flow: CardBodyFlow) {
  const text = cleanReaderText(body).replace(/\r/g, '').trim();
  if (!text) return [];
  const blocks = flow === 'paragraphs'
    ? text.split(/\n{2,}/).map(block => block.replace(/\n+/g, ' ').trim())
    : text.split('\n').map(line => line.trim());
  return blocks.filter(line => line && !SYSTEM_COPY_LINES.has(line));
}

function cleanLine(line: string) {
  return line.replace(/^\s*\d+[.、]\s*/, '');
}

function parseCompareRows(body?: string | null) {
  return getBodyLines(body).map(line => {
    const parts = line.split('|').map(part => part.trim()).filter(Boolean);
    if (parts.length >= 2) return [parts[0], parts.slice(1).join(' | ')];
    return [line, '更稳的做法'];
  });
}

function getComponentKey(card: Card): string {
  const value = card.style_json?.component_key;
  if (typeof value === 'string' && COMPONENT_KEYS.includes(value)) return value;
  if (card.card_type === 'cover') return 'hero_cover';
  if (card.card_type === 'summary' || card.layout_key === 'summary') return 'summary_cta';
  if (card.layout_key === 'workflow_steps') return 'flow_steps';
  return 'icon_points';
}

function getCoverFallback(card: Card) {
  const haystack = `${card.title || ''} ${card.subtitle || ''}`.toLowerCase();
  if (haystack.includes('坑') || haystack.includes('避坑')) return '先看哪里容易翻车，再看更稳的做法';
  if (haystack.includes('github') || haystack.includes('开源') || haystack.includes('langgraph')) return '从问题、适合谁、怎么复用三个角度拆开看';
  if (haystack.includes('工具') || haystack.includes('测评')) return '不看热度，先看它能不能解决你的任务';
  if (haystack.includes('日报') || haystack.includes('工作流') || haystack.includes('流程')) return '先跑通一个小流程，再考虑自动化升级';
  return '把问题拆开，给出能照着做的下一步';
}

function drawWrappedText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
  maxLines: number,
): number {
  const normalized = text.replace(/\r/g, '').split('\n');
  const lines: string[] = [];
  for (const paragraph of normalized) {
    let current = '';
    for (const char of paragraph) {
      const next = current + char;
      if (ctx.measureText(next).width > maxWidth && current) {
        lines.push(current);
        current = char;
      } else {
        current = next;
      }
    }
    if (current) lines.push(current);
  }

  const visible = lines.slice(0, maxLines);
  visible.forEach((line, index) => {
    const suffix = index === maxLines - 1 && lines.length > maxLines ? '...' : '';
    ctx.fillText(line + suffix, x, y + index * lineHeight);
  });
  return visible.length;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + width - radius, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
  ctx.lineTo(x + width, y + height - radius);
  ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  ctx.lineTo(x + radius, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
}

function fitFontSize(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, start: number, min: number) {
  for (let size = start; size >= min; size -= 2) {
    ctx.font = `800 ${size}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    const longest = text.split(/\s|\n/).reduce((max, chunk) => Math.max(max, ctx.measureText(chunk).width), 0);
    if (longest <= maxWidth) return size;
  }
  return min;
}

function drawBackgroundPattern(ctx: CanvasRenderingContext2D, theme: CardTheme) {
  ctx.save();
  ctx.globalAlpha = 0.2;
  ctx.strokeStyle = theme.line;
  ctx.lineWidth = 1;
  for (let y = 178; y < CARD_HEIGHT - 160; y += 86) {
    ctx.beginPath();
    ctx.moveTo(72, y);
    ctx.lineTo(CARD_WIDTH - 72, y);
    ctx.stroke();
  }

  ctx.globalAlpha = 0.16;
  ctx.fillStyle = theme.accent;
  ctx.translate(CARD_WIDTH - 110, 132);
  ctx.rotate(-Math.PI / 18);
  roundRect(ctx, -110, -20, 220, 38, 19);
  ctx.fill();
  ctx.restore();
}

function download(dataUrl: string, filename: string) {
  const link = document.createElement('a');
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function safeName(input: string) {
  return input.replace(/[\\/:*?"<>|]/g, '').replace(/\s+/g, '-').slice(0, 24) || 'xiaohongshu';
}

export interface ExportPackage {
  cards: Card[];
  titleOptions?: string[];
  bodyText?: string;
  hashtags?: string[];
  commentGuide?: string;
}

export async function exportCardsZip(pkg: ExportPackage, draftId: number) {
  const JSZip = (await import('jszip')).default;
  const { saveAs } = await import('file-saver');
  const zip = new JSZip();

  const cardsFolder = zip.folder('cards')!;

  // 导出每张卡片 PNG
  for (const card of pkg.cards) {
    const dataUrl = renderCardToDataUrl(card, pkg.cards.length);
    if (dataUrl) {
      const base64 = dataUrl.split(',')[1];
      const pad = String(card.page_index).padStart(2, '0');
      cardsFolder.file(`${pad}-${card.card_type}.png`, base64, { base64: true });
    }
  }

  // 文案文件
  const copyFolder = zip.folder('copy')!;
  if (pkg.titleOptions?.length) {
    copyFolder.file('title-options.txt', pkg.titleOptions.map((t, i) => `${i + 1}. ${t}`).join('\n'));
  }
  if (pkg.bodyText) {
    copyFolder.file('body.txt', pkg.bodyText);
  }
  if (pkg.hashtags?.length) {
    copyFolder.file('hashtags.txt', pkg.hashtags.join(' '));
  }
  if (pkg.commentGuide) {
    copyFolder.file('comment-guide.txt', pkg.commentGuide);
  }

  const blob = await zip.generateAsync({ type: 'blob' });
  saveAs(blob, `xhs-package-draft-${draftId}.zip`);
}

// ZIP 导出用的渲染函数（返回 data URL，不触发下载）
