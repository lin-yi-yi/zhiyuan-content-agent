export const PAGES = ['overview', 'brands', 'knowledge', 'source-hub', 'evidence', 'agent', 'rag', 'drafts', 'metrics', 'reports', 'pilot', 'connections', 'settings', 'team', 'billing', 'account', 'sources', 'topics'] as const;
export type Page = typeof PAGES[number];
export interface WorkbenchRoute { page: Page; params: URLSearchParams }
const ID_KEYS = new Set(['brand', 'kb', 'run', 'draft', 'topic']);
const WORKFLOWS = new Set(['knowledge_post', 'product_faq', 'case_story']);
const RETIRED_PAGES = new Set(['learning', 'portfolio', 'architecture']);
const validId = (value: string) => /^[1-9]\d{0,9}$/.test(value) && Number.isSafeInteger(Number(value));

export function parseWorkbenchRoute(value: string): WorkbenchRoute {
  const normalized = value.replace(/^#/, '');
  const separator = normalized.indexOf('?');
  const raw = separator < 0 ? normalized : normalized.slice(0, separator);
  const query = separator < 0 ? '' : normalized.slice(separator + 1);
  if (RETIRED_PAGES.has(raw) || raw.startsWith('learning:') || !PAGES.includes(raw as Page)) {
    return {page: 'overview', params: new URLSearchParams()};
  }
  const params = new URLSearchParams();
  for (const [key, value] of new URLSearchParams(query)) {
    if (ID_KEYS.has(key) && validId(value)) params.set(key, value);
    else if (key === 'workflow' && WORKFLOWS.has(value)) params.set(key, value);
  }
  return {page: raw as Page, params};
}

export function routeHash(route: WorkbenchRoute) {
  const safe = parseWorkbenchRoute(`${route.page}?${route.params.toString()}`);
  const query = safe.params.toString();
  return `#${safe.page}${query ? `?${query}` : ''}`;
}

export function routeNumber(route: WorkbenchRoute, key: string) {
  const value = route.params.get(key);
  return ID_KEYS.has(key) && value && validId(value) ? Number(value) : null;
}

export function brandKnowledgeRoute(savedBrand: {knowledge_base_id: number} | null, newBrandKnowledgeBaseId?: number | null) {
  // An unsaved edit must not replace an existing brand's persisted binding.
  const id = savedBrand?.knowledge_base_id ?? newBrandKnowledgeBaseId;
  return id != null && validId(String(id)) ? `knowledge?kb=${id}` : 'knowledge';
}
