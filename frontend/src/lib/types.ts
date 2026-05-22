// ── Enums ──────────────────────────────────────────────────────────────────
export type SourceType = "resume" | "linkedin" | "portfolio" | "article" | "other";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "do_not_use";
export type ProvenanceColor = "green" | "blue" | "yellow" | "red";
export type WorkMode = "remote" | "onsite" | "hybrid" | "any";
export type Aggressiveness = "conservative" | "balanced" | "opportunistic";
export type BulletStatus = "proposed" | "accepted" | "rejected" | "rewritten" | "locked";
export type PackageStatus = "draft" | "ready" | "exported";
export type ApplicationStatus =
  | "drafted" | "exported" | "submitted_by_user"
  | "interviewing" | "offer" | "rejected" | "withdrawn";

// ── Sources & Claims ───────────────────────────────────────────────────────
export interface Source {
  id: string; source_type: SourceType; label: string;
  filename?: string | null; extracted_text: string;
  parse_meta: Record<string, unknown>; created_at: string; claim_count: number;
}
export interface SourceRef {
  id: string; source_id: string; source_type: SourceType;
  section: string; snippet: string; page?: number | null; confidence: number;
}
export interface Claim {
  id: string; source_id: string; claim_text: string; claim_type: string;
  company?: string | null; role?: string | null; date_range?: string | null;
  skills: string[]; metrics: string[]; confidence: number;
  approval_status: ApprovalStatus; user_note?: string | null;
  provenance_category: string; provenance_color: ProvenanceColor;
  source_refs: SourceRef[];
}

// ── Strategy ───────────────────────────────────────────────────────────────
export interface Strategy {
  id: string; name: string; is_active: boolean; target_roles: string[];
  region?: string | null; work_mode: WorkMode;
  salary_min?: number | null; salary_max?: number | null;
  aggressiveness: Aggressiveness; weights: Record<string, number>;
  include_companies: string[]; exclude_companies: string[];
  targeting_notes: string; score_threshold: number; updated_at: string;
}
export interface StrategyListItem {
  id: string; name: string; is_active: boolean; aggressiveness: Aggressiveness;
  score_threshold: number; target_roles: string[]; work_mode: WorkMode; updated_at: string;
}
export interface StrategyPreviewCounts {
  jobs_considered: number; above_threshold: number; strong: number;
  moderate: number; weak: number; excluded: number;
  avg_score: number; top_score: number; score_threshold: number;
  threshold_passing_titles: string[];
}
export interface StrategyPreview {
  strategy_id: string | null; strategy_name: string;
  current: StrategyPreviewCounts; active: StrategyPreviewCounts | null; summary: string;
}
export interface StrategyUpsertBody {
  name: string; target_roles: string[]; region?: string | null; work_mode: WorkMode;
  salary_min?: number | null; salary_max?: number | null;
  aggressiveness: Aggressiveness; weights: Record<string, number>;
  include_companies: string[]; exclude_companies: string[];
  targeting_notes: string; score_threshold: number; activate?: boolean;
}
export interface SeedPresetsResult {
  created: StrategyListItem[]; skipped_existing: string[]; note: string;
}

// ── Jobs ───────────────────────────────────────────────────────────────────
export interface Job {
  id: string; title: string; company: string; location?: string | null;
  work_mode: WorkMode; salary_min?: number | null; salary_max?: number | null;
  source: string; source_url?: string | null; description_text: string;
  requirements: string[];
  structured_requirements: {
    must_have?: string[]; nice_to_have?: string[];
    min_years?: number | null; seniority_rank?: number | null;
    skills?: string[]; domains?: string[];
  };
  is_archived: boolean; deduplicated: boolean;
  posted_at?: string | null; imported_at: string;
  // Phase 5: provider tracking + freshness
  provider_source?: string | null; provider_job_id?: string | null;
  last_seen_at?: string | null; is_stale?: boolean;
}

// ── Matches ────────────────────────────────────────────────────────────────
export interface ScoreComponent {
  key: string; label: string; weight: number; earned: number; detail: string;
  evidence?: { claim_id: string; snippet: string }[];
}
export interface SemanticSignal {
  provider: string; similarity: number; affects_score: boolean;
  agreement: string; note: string;
}
export interface Match {
  job: Job; score: number; earned_points: number; max_points: number;
  components: ScoreComponent[]; matched_skills: string[];
  missing_requirements: string[]; excluded: boolean; summary: string;
  structured_requirements?: Record<string, unknown>;
  semantic?: SemanticSignal | null;
}

// Phase 5: filter type for Match Inbox
export type MatchFilter =
  | "all" | "strong" | "moderate" | "stretch"
  | "remote" | "above_target" | "new_this_week" | "missing_req" | "stale";

// ── Phase 5: Saved Searches ────────────────────────────────────────────────
export interface SavedSearch {
  id: string; owner_id: string; name: string; query: string;
  provider?: string | null; min_salary?: number | null; max_salary?: number | null;
  work_mode?: string | null; location_filter?: string | null;
  frequency: "manual" | "daily" | "weekly";
  last_run_at?: string | null; created_at: string; is_active: boolean;
}
export interface SavedSearchRunResult {
  search_id: string; search_name: string; provider_used: string;
  jobs_fetched: number; jobs_created: number; jobs_skipped_dupes: number;
  last_run_at: string;
}

// ── Packages ───────────────────────────────────────────────────────────────
export interface PackageBullet {
  id: string; section: string; current_text: string; original_text?: string | null;
  order_index: number; status: BulletStatus; provenance_color: ProvenanceColor;
  provenance_label?: string; claim_id?: string | null;
  source_snippet?: string | null; flagged?: string[];
}
export interface PackageDetail {
  id: string; title: string; company: string; job_id: string;
  score_snapshot: number; status: string; summary?: string;
  bullets: PackageBullet[]; created_at?: string;
}
export interface PackageListItem {
  id: string; title: string; company: string; score_snapshot: number; status: string;
}
export interface ExportPreview {
  title: string; company: string; job_title: string; score: number;
  summary?: string; generated_at: string; include_unsupported: boolean;
  sections: Record<string, {
    text: string; provenance: string; color: ProvenanceColor;
    status: string; flagged?: string[];
  }[]>;
  excluded: { text: string; section: string; reasons: string[] }[];
}

// ── Applications / Tracker ─────────────────────────────────────────────────
export interface Application {
  id: string; package_id: string; job_title: string; company: string;
  status: ApplicationStatus; applied_at?: string | null;
  submitted_at?: string | null; snapshot_sha?: string | null;
  history: { status: string; at: string; note?: string }[];
  reminders: {
    id: string; due_at: string; offset_days: number;
    kind: string; message: string; done: boolean;
  }[];
  notes?: string;
}
export interface ApplySession {
  id: string; package_id: string; job_title: string; company: string;
  state: string; requires_handoff: boolean; note: string;
  plan: { step: number; field: string; value: string; source: string; needs_user: boolean; note?: string }[];
  history: { state: string; note: string; at: string }[];
  allowed_actions: string[]; guardrails: string[]; created_at: string;
}

// ── Onboarding ─────────────────────────────────────────────────────────────
export interface OnboardingStatus {
  completed: number; total: number;
  steps: { key: string; label: string; done: boolean }[];
  next_step?: string | null;
}

// ── Auth ───────────────────────────────────────────────────────────────────
export interface Me {
  id: string; email: string; name: string; is_default: boolean; auth_enabled: boolean;
}
export interface AuthSuccess {
  id: string; email: string; name: string; token: string;
}

// ── Health ─────────────────────────────────────────────────────────────────
export interface Health {
  status: string; app: string; slice?: string; phase?: number;
  phases_shipped?: number[]; latest_phase?: number;
  upgrade_phases_shipped?: number[];
  ingestion_formats?: string[]; export_formats?: string[];
  providers?: { ai?: string; embedding?: string; job?: string; search?: string; notification?: string };
  auth?: { enabled: boolean };
  ai_assist?: { grounding_gate: boolean; auto_apply: boolean };
  application_tracker?: { auto_submit: boolean; immutable_snapshot: boolean };
  observability?: {
    structured_logs: boolean; request_id: boolean;
    audit_trail: boolean; config_validation: boolean;
  };
}

// ── Audit ──────────────────────────────────────────────────────────────────
export interface AuditEvent {
  id: string; method: string; path: string; status: number;
  duration_ms: number; at: string; request_id: string;
}

// ── Job Sources ────────────────────────────────────────────────────────────
export interface JobSourceInfo { id: string; mock: boolean; sample_count: number; }
export interface JobSources { active_provider: string; available: JobSourceInfo[]; }
