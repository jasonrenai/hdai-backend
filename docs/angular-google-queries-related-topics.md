# Angular: Google Queries list — search & relatedTopics

Guide for wiring the updated `get-all-google-queries` API into an existing Angular list page.

## API

```
GET /api/v1/google-query-scraper/get-all-google-queries
```

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `page` | number | no (default `1`) | 1-based page |
| `limit` | number | no (default `10`) | Page size (max `500`) |
| `search` | string | no | Case-insensitive match on query text |
| `relatedTopics` | string \| string[] | no | Match **any** topic. Repeat the param and/or comma-separate |

Auth: same JWT as today.

### Example URLs

```text
?page=1&limit=10
?page=1&limit=10&search=marketing
?page=1&limit=10&relatedTopics=AI
?page=1&limit=10&relatedTopics=AI&relatedTopics=Marketing
?page=1&limit=10&search=speakers&relatedTopics=AI,Marketing
```

### Response shape (unchanged wrapper)

```ts
{
  success: true,
  data: {
    googleQueries: GoogleQueryItem[];
    total: number;
    page: number;
    limit: number;
    totalPages: number;
  },
  error: null
}
```

### New / important field on each item

```ts
interface GoogleQueryItem {
  _id: string;
  query: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | string;
  relatedTopics: string[];  // always an array (may be empty)
  // ...existing fields: urls, urlCollectionIds, createdAt, etc.
}
```

Catalog topic names (exact strings used for filter + display):

`AI`, `B2B`, `B2C`, `Communications`, `Customer Experience`, `Data Science`, `Developer`, `E-Commerce`, `EdTech`, `Education`, `Entrepreneurship`, `Executive Leadership`, `Financial Services`, `Franchise`, `Health`, `Human Resources (HR)`, `Marketing`, `Nonprofit`, `Public Relations (PR)`, `Remortgage`, `Retail`, `Technology`, `UX/UI`, `Women In Tech`

Prefer loading these from your existing speaker topics API if the page already uses it; otherwise hardcode or copy this list for the filter chips/select.

---

## 1. Update the service method

Pass optional `search` and `relatedTopics`. Use `HttpParams` so repeated `relatedTopics` serialize correctly.

```ts
// google-query.service.ts (adapt to your existing service name/paths)

getAllGoogleQueries(params: {
  page?: number;
  limit?: number;
  search?: string;
  relatedTopics?: string[];
}): Observable<any> {
  let httpParams = new HttpParams()
    .set('page', String(params.page ?? 1))
    .set('limit', String(params.limit ?? 10));

  const search = (params.search || '').trim();
  if (search) {
    httpParams = httpParams.set('search', search);
  }

  for (const topic of params.relatedTopics || []) {
    const t = (topic || '').trim();
    if (t) {
      httpParams = httpParams.append('relatedTopics', t);
    }
  }

  return this.http.get(
    `${this.baseUrl}/api/v1/google-query-scraper/get-all-google-queries`,
    { params: httpParams }
  );
}
```

---

## 2. Component state

Add search + topic filter state next to your existing pagination.

```ts
page = 1;
limit = 10;
search = '';
selectedTopics: string[] = [];

googleQueries: GoogleQueryItem[] = [];
total = 0;
totalPages = 0;
loading = false;

// Optional: debounce search input with Subject + debounceTime(300)
private search$ = new Subject<void>();

ngOnInit(): void {
  this.search$
    .pipe(debounceTime(300), takeUntilDestroyed()) // or takeUntil(destroy$)
    .subscribe(() => {
      this.page = 1;
      this.load();
    });
  this.load();
}

onSearchChange(): void {
  this.search$.next();
}

onTopicsChange(topics: string[]): void {
  this.selectedTopics = topics;
  this.page = 1;
  this.load();
}

load(): void {
  this.loading = true;
  this.googleQueryService
    .getAllGoogleQueries({
      page: this.page,
      limit: this.limit,
      search: this.search,
      relatedTopics: this.selectedTopics,
    })
    .subscribe({
      next: (res) => {
        const data = res?.data ?? res;
        this.googleQueries = data.googleQueries ?? [];
        this.total = data.total ?? 0;
        this.totalPages = data.totalPages ?? 0;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
}
```

Keep your existing page-change handler; only reset `page` to `1` when search/topics change.

---

## 3. Template additions

Minimal controls + display chips. Fit these into your current layout (table/cards).

```html
<!-- Search -->
<input
  type="search"
  placeholder="Search queries..."
  [(ngModel)]="search"
  (ngModelChange)="onSearchChange()"
/>

<!-- Topic filter — use mat-select / chips / your design system -->
<select
  multiple
  [ngModel]="selectedTopics"
  (ngModelChange)="onTopicsChange($event)"
>
  <option *ngFor="let t of allTopics" [value]="t">{{ t }}</option>
</select>

<!-- Existing table: add a Related topics column -->
<td>
  <span
    *ngFor="let topic of row.relatedTopics || []"
    class="topic-chip"
  >
    {{ topic }}
  </span>
  <span *ngIf="!(row.relatedTopics?.length)">—</span>
</td>
```

If you use Angular Material:

```html
<mat-form-field>
  <mat-label>Related topics</mat-label>
  <mat-select
    multiple
    [(ngModel)]="selectedTopics"
    (ngModelChange)="onTopicsChange($event)"
  >
    <mat-option *ngFor="let t of allTopics" [value]="t">{{ t }}</mat-option>
  </mat-select>
</mat-form-field>
```

---

## 4. Checklist

- [ ] Extend list request with `search` / `relatedTopics`
- [ ] Reset to page `1` when filters change
- [ ] Debounce text search (~300ms)
- [ ] Render `relatedTopics` chips/tags in the row
- [ ] Use exact catalog topic strings in the filter (not free text)
- [ ] Handle empty `relatedTopics: []` (show em dash / “None”)

No breaking change: calling the old URL with only `page` & `limit` still works; `relatedTopics` is simply present on each record for display.
