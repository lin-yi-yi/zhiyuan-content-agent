import { test, expect, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dataset = JSON.parse(await fs.readFile(path.join(root, 'scripts/fixtures/industrial_faq_synthetic.json'), 'utf8'));
const material = dataset.documents.find((item: { key: string }) => item.key === 'spec');
const source = 'https://example.com/synthetic-industrial/spec';
const brandName = 'E2E 合成星桥工业设备（虚构）';
const editMarker = '【浏览器重审版本】此段仅验证编辑与审批状态，不增加任何产品事实。';

async function mainNavigation(page: Page, name: string) {
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: new RegExp(name) }).click();
}

async function downloadDelivery(page: Page, testInfo: TestInfo, filename: string) {
  const pendingDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载审核交付清单', exact: true }).click();
  const download = await pendingDownload;
  expect(download.suggestedFilename()).toMatch(/^交付清单-任务\d+\.md$/);
  const destination = testInfo.outputPath(filename);
  await download.saveAs(destination);
  await testInfo.attach(filename, { path: destination, contentType: 'text/markdown' });
  const markdown = await fs.readFile(destination, 'utf8');
  expect(markdown).toContain('# 已审核内容交付单');
  expect(markdown).toContain(`品牌：${brandName}`);
  expect(markdown).toContain(`资料版本：${material.version_label}`);
  expect(markdown).toContain(`原文定位：${material.citations[0].locator}`);
  expect(markdown).toContain(material.title);
  expect(markdown).toContain(source);
  expect(markdown).toContain('核验状态：人工核验有效');
  expect(markdown).toMatch(/\[chunk:\d+\]/);
  expect(markdown).toContain('并非生成时元数据快照');
  const hash = markdown.match(/内容快照 SHA-256：([a-f0-9]{64})/)?.[1];
  expect(hash).toBeTruthy();
  return { markdown, hash };
}

test('页面核验资料、生成 FAQ、正式下载及编辑后重新审核', async ({ page, request, baseURL }, testInfo) => {
  const blockedBrowserRequests: string[] = [];
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.context().route('**/*', async route => {
    const address = new URL(route.request().url());
    if (address.origin === baseURL || ['data:', 'blob:'].includes(address.protocol)) return route.continue();
    blockedBrowserRequests.push(`${address.protocol}//${address.host}`);
    return route.abort('blockedbyclient');
  });

  await test.step('确认空白隔离环境，未读取用户资料或真实模型配置', async () => {
    const response = await request.get('/__e2e__/isolation');
    expect(response.ok()).toBeTruthy();
    expect(await response.json()).toEqual({
      fixture: 'zhiyuan-browser-regression', temporary_database: true,
      retrieval_mode: 'lexical', generation_provider: 'local', document_count: 0,
      external_model_runs: 0, outgoing_connection_attempts: 0,
    });
    await page.goto('/');
    await expect(page.getByText('本地 · 免登录', { exact: true })).toBeVisible();
  });

  await test.step('通过页面填写合成核验笔记，人工确认后单独入库', async () => {
    await mainNavigation(page, '品牌与资料');
    await page.getByText('更多', { exact: true }).click();
    await page.getByRole('link', { name: '核验笔记', exact: true }).click();
    await page.getByLabel('标题', { exact: true }).fill(material.title);
    await page.getByLabel('来源版本', { exact: true }).fill(material.version_label);
    await page.getByLabel('主要来源链接', { exact: true }).fill(source);
    await page.getByLabel('本人笔记（至少 40 字）', { exact: true }).fill(material.content);
    await page.getByLabel('内容使用依据', { exact: true }).selectOption('own');
    await page.getByLabel('使用依据说明', { exact: true }).fill('仓库自编合成数据，仅用于浏览器回归。');
    for (const [index, citation] of material.citations.entries()) {
      if (index) await page.getByRole('button', { name: '添加引用', exact: true }).click();
      const editor = page.locator('.evidence-citation-edit').nth(index);
      await editor.getByLabel('品牌及产品型号（产品参数时填写）', { exact: true }).fill(citation.product_model);
      await editor.getByLabel('参数名及适用条件', { exact: true }).fill(citation.parameter);
      await editor.getByLabel('参数值（含单位）', { exact: true }).fill(citation.value);
      await editor.getByLabel('需要核对的结论', { exact: true }).fill(citation.claim);
      await editor.getByLabel('支持该结论的原文摘录', { exact: true }).fill(citation.excerpt);
      await editor.getByLabel('该条证据的来源链接', { exact: true }).fill(`${source}#paragraph-1`);
      await editor.getByLabel('定位说明（章节、段落或页码）', { exact: true }).fill(citation.locator);
    }
    await page.getByRole('button', { name: '保存为待核验笔记', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('笔记已保存为待核验');
    await expect(page.getByRole('button', { name: '加入知识库', exact: true })).toHaveCount(0);
    await page.getByRole('checkbox', { name: '我已查看所列来源，核对结论、摘录及适用条件，并确认内容的使用依据。', exact: true }).check();
    await page.getByRole('button', { name: '确认已核验', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('人工核验已记录');
    await page.getByRole('button', { name: '加入知识库', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('笔记已加入知识库');
    await page.getByRole('link', { name: '知识资料', exact: true }).click();
    await expect(page.locator('.knowledge-document-row')).toHaveCount(1);
    await expect(page.locator('.knowledge-document-row')).toContainText(material.title);
    await expect(page.locator('.knowledge-document-row')).toContainText('含核验记录');
  });

  await test.step('通过页面保存品牌并创建有明确必需参数的本地 FAQ', async () => {
    await page.getByRole('link', { name: '品牌档案', exact: true }).click();
    await page.getByLabel('品牌 / 客户名称', { exact: true }).fill(brandName);
    await page.getByLabel('内容主要写给谁', { exact: true }).fill('合成演示中的售前人员');
    await page.getByText('内容边界与行动引导', { exact: true }).click();
    await page.getByLabel('品牌任务的生成方式', { exact: true }).selectOption('local_only');
    await page.getByRole('button', { name: '保存品牌档案', exact: true }).click();
    await expect(page.getByRole('status')).toContainText(`已保存「${brandName}」第 1 版`);
    await page.getByRole('button', { name: '用已保存档案创建内容 →', exact: true }).click();
    await page.getByRole('button', { name: '产品与服务答疑', exact: true }).click();
    await page.getByLabel('本次内容目标', { exact: true }).fill('合成星桥 XP-24 额定电压与额定流量答疑');
    await expect(page.getByLabel('生成方式', { exact: true })).toHaveValue('local');
    await page.getByText('必须有依据的产品参数（可选）', { exact: true }).click();
    await page.getByLabel('品牌及产品型号', { exact: true }).fill('合成星桥 XP-24');
    await page.getByLabel('必需参数 · 每行一项，最多 10 项', { exact: true }).fill('额定电压\n额定流量');
    await page.getByRole('button', { name: '生成内容草稿 →', exact: true }).click();
    await expect(page.getByRole('heading', { name: '等待你审核', exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: '下载审核交付清单', exact: true })).toHaveCount(0);
    await page.getByText(/^查看依据 ·/).click();
    await expect(page.locator('.task-citation')).toContainText(material.title);
    await expect(page.locator('.task-citation')).toContainText('24 V DC');
    await expect(page.locator('.task-citation')).toContainText('12 L/min');
  });

  const runId = new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('run');
  expect(runId).toMatch(/^\d+$/);
  let first: Awaited<ReturnType<typeof downloadDelivery>>;
  await test.step('人工批准后实际下载正式 Markdown，核对资料版本及引用', async () => {
    const beforeApproval = await request.get(`/api/agent-runs/${runId}/delivery`);
    expect(beforeApproval.status()).toBe(409);
    await page.getByLabel('审核备注', { exact: true }).fill('浏览器合成审核第 1 次：已核对参数与引用。');
    await page.getByRole('button', { name: '审核通过', exact: true }).click();
    await expect(page.getByRole('heading', { name: '审核通过', exact: true })).toBeVisible();
    first = await downloadDelivery(page, testInfo, 'approved-v1.md');
    expect(first.markdown).not.toContain(editMarker);
    expect(first.markdown).not.toContain('浏览器合成审核第 1 次');
  });

  await test.step('从关联任务打开稿件并保存修改，旧批准与正式交付立即失效', async () => {
    await page.getByRole('button', { name: '打开交付内容 →', exact: true }).click();
    await expect(page.getByRole('heading', { name: '稿件编辑', exact: true })).toBeVisible();
    const body = page.getByRole('tabpanel', { name: /正文/ }).getByRole('textbox', { name: /^正文/ });
    await expect(body).toHaveValue(/./);
    const original = await body.inputValue();
    expect(original).toMatch(/\[chunk:\d+\]/);
    await body.fill(`${original}\n\n${editMarker}`);
    await page.getByRole('button', { name: '保存稿件', exact: true }).click();
    await expect(page.getByText(/稿件 #\d+ 已保存。/)).toBeVisible();
    await page.getByRole('button', { name: `返回审核任务 #${runId}`, exact: true }).click();
    await expect(page.getByRole('heading', { name: '等待你审核', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '下载审核交付清单', exact: true })).toHaveCount(0);
    const staleDelivery = await request.get(`/api/agent-runs/${runId}/delivery`);
    expect(staleDelivery.status()).toBe(409);
  });

  await test.step('页面重新审核后再次下载，仅交付新的内容版本', async () => {
    await page.getByLabel('审核备注', { exact: true }).fill('浏览器合成审核第 2 次：重新核对编辑后内容。');
    await page.getByRole('button', { name: '审核通过', exact: true }).click();
    await expect(page.getByRole('heading', { name: '审核通过', exact: true })).toBeVisible();
    const second = await downloadDelivery(page, testInfo, 'approved-v2.md');
    expect(second.markdown).toContain(editMarker);
    expect(second.hash).not.toBe(first.hash);
    const current = await request.get(`/api/agent-runs/${runId}`);
    const run = await current.json();
    expect(run.status).toBe('approved');
    expect(run.result_json.review.content_hash).toBe(second.hash);
    expect(second.markdown).toContain(run.draft.body_text);
    expect(run.result_json.review_history.some((entry: { decision: string }) => entry.decision === 'approve')).toBeTruthy();
  });

  await test.step('确认浏览器和后端没有外发或调用在线模型', async () => {
    const isolation = await (await request.get('/__e2e__/isolation')).json();
    expect(isolation.document_count).toBe(1);
    expect(isolation.external_model_runs).toBe(0);
    expect(isolation.outgoing_connection_attempts).toBe(0);
    expect(blockedBrowserRequests).toEqual([]);
    expect(browserErrors).toEqual([]);
    await testInfo.attach('isolation.json', { body: JSON.stringify(isolation, null, 2), contentType: 'application/json' });
  });
});
