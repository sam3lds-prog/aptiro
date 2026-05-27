// Aptiro TypeScript types — Phase 8 update.
// Hand-maintained against FastAPI Pydantic schemas.

// ── Auth ───────────────────────────────────────────────────────────────────
export interface Me {
  id: string;
  email: string;
  name: string;
  is_default: boolean;
  auth_enabled: boolean;
}

export interface AuthOut {
  id: string;
  email: string;
  name: string;
  token: string;
}

// Phase 8
export interface RotateOut {
  token: string;
  expires_at: string | null;
}

export interface SignedExportLink {
  token: string;
  url: string;
  expires_at: string;
  format: string;
  artifact: string;
}

export interface LegalDoc {
  content: string;
  format: "markdown";
  last_updated: string;
}

// ── Health ─────────────────────────────────────────────────────────────────
export interface Health {
  status: string;
  app: string;
  phase?: number;
  phases_shipped?: number[];
  latest_phase?: number;
  upgrade_phases_shipped?: number[];
  ingestion_formats?: string[];
  export_formats?: string[];
  providers?: {
    ai?: string;
    embedding?: string;
    job?: string;
    search?: string;
    notification?: string;
  };
  auth?: { enabled: boolean };
  ai_assist?: { grounding_gate: boolean; auto_apply: boolean };
  application_tracker?: { auto_submit: boolean; immutable_snapshot: boolean };
  observability?: {
    structured_logs: boolean;
    request_id: boolean;
    audit_trail: boolean;
    config_validation: boolean;
  };
}

// ── Audit ──────────────────────────────────────────────────────────────────
export interface AuditEvent {
  id: string;
  method: string;
  path: string;
  status: number;
  duration_ms: number;
  at: string;
  request_id: string;
}

// ── Notifications (legacy preview, unchanged) ──────────────────────────────
export type NotificationKind =
  | "package_ready"
  | "daily_digest"
  | "integrity_alert"
  | "weekly_digest"
  | "match_threshold_alert"
  | "followup_reminder";
export type NotificationChannel = "email" | "slack" | "in_app";
export interface NotificationPreview {
  id: string;
  kind: NotificationKind;
  channel: NotificationChannel;
  subject: string;
  body: string;
  package_id?: string;
  at: string;
}

// ── Phase 7 notifications ──────────────────────────────────────────────────
export interface NotificationPreferences {
  in_app_enabled: boolean;
  email_enabled: boolean;
  email_address: string;
  email_daily_digest: boolean;
  email_weekly_digest: boolean;
  match_alert_threshold: number;
  sms_enabled: boolean;
  sms_phone: string;
  smtp_configured: boolean;
  twilio_configured: boolean;
}

export interface InAppNotification {
  id: string;
  kind: NotificationKind;
  subject: string;
  body: string;
  is_read: boolean;
  at: string;
  package_id?: string;
}

export interface InboxResponse {
  unread_count: number;
  items: InAppNotification[];
}

// ── Sources ────────────────────────────────────────────────────────────────
export type SourceType =
  | "resume"
  | "linkedin"
  | "portfolio"
  | "public_article"
  | "manual_note";

export interface Source {
  id: string;
  source_type: SourceType;
  label: string;
  url?: string;
  raw_text?: string;
  parse_meta?: Record<string, unknown>;
  claim_count: number;
  created_at: string;
}

// ── Claims ─────────────────────────────────────────────────────────────────
export type ApprovalStatus = "pending" | "approved" | "rejected" | "do_not_use";
export type ProvenanceColor = "blue" | "purple" | "green" | "orange" | "red";

export interface SourceRef {
  id: string;
  source_id: string;
  snippet: string;
  section?: string;
  page?: number;
}

export interface Claim {
  id: string;
  claim_text: string;
  claim_type: string;
  confidence: number;
  provenance_color: ProvenanceColor;
  approval_status: ApprovalStatus;
  source_id?: string;
  source_refs: SourceRef[];
  has_metric: boolean;
  metric_supported: boolean;
  risk_flag?: string;
  created_at: string;
}

// ── Strategy ───────────────────────────────────────────────────────────────
export interface Strategy {
  id: string;
  name: string;
  target_role: string;
  target_industry: string;
  target_location: string;
  comp_min?: number;
  comp_max?: number;
  weights: Record<string, number>;
  risk_tolerance: string;
  score_threshold: number;
  is_active: boolean;
  created_at: string;
}

// ── Jobs ───────────────────────────────────────────────────────────────────
export interface JobPosting {
  id: string;
  title: string;
  company: string;
  location?: string;
  description?: string;
  requirements: string[];
  structured_requirements?: Record<string, unknown>;
  salary_min?: number;
  salary_max?: number;
  remote?: boolean;
  source?: string;
  source_url?: string;
  posted_at?: string;
  is_archived: boolean;
  is_stale?: boolean;
  provider?: string;
  external_id?: string;
  created_at: string;
}

// ── Matches ────────────────────────────────────────────────────────────────
export interface MatchComponent {
  name: string;
  score: number;
  weight: number;
  explanation: string;
}

export interface Match {
  job_id: string;
  job_title: string;
  company: string;
  location?: string;
  score: number;
  confidence: string;
  top_reasons: string[];
  top_risk?: string;
  components: MatchComponent[];
  matched_skills: string[];
  skill_gaps: string[];
  salary_min?: number;
  salary_max?: number;
  remote?: boolean;
  source_url?: string;
  posted_at?: string;
  is_stale?: boolean;
  strategy_used?: string;
  semantic_similarity?: number;
  semantic_label?: string;
}

// ── Packages ───────────────────────────────────────────────────────────────
export type BulletStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "do_not_use"
  | "locked";

export interface PackageBullet {
  id: string;
  section: string;
  current_text: string;
  original_text: string;
  status: BulletStatus;
  provenance_color: ProvenanceColor;
  claim_id?: string;
  order_index: number;
  risk_flag?: string;
  lock_reason?: string;
}

export interface AgentCritique {
  agent: string;
  severity: string;
  message: string;
  note?: string;
}

export interface AgentRun {
  id: string;
  status: string;
  ready: boolean;
  summary: string;
  readiness_score: number;
  critiques: AgentCritique[];
  steps?: number;
  flags?: string[];
  created_at: string;
}

export interface Package {
  id: string;
  title: string;
  company: string;
  job_id: string;
  score_snapshot?: number;
  summary?: string;
  cover_letter?: string;
  bullets: PackageBullet[];
  latest_run?: AgentRun;
  created_at: string;
}

export interface ExportPreviewSection {
  section: string;
  bullets: string[];
}

export interface ExportPreviewExcluded {
  text: string;
  reason: string;
}

export interface ExportPreview {
  title: string;
  company: string;
  sections: ExportPreviewSection[];
  excluded: ExportPreviewExcluded[];
  include_unsupported: boolean;
  generated_at: string;
}

// ── Application tracker ────────────────────────────────────────────────────
export type ApplicationStatus =
  | "drafted"
  | "exported"
  | "submitted_by_user"
  | "interviewing"
  | "offer"
  | "rejected"
  | "withdrawn";

export interface ApplicationReminder {
  id: string;
  due_date: string;
  label: string;
  done: boolean;
}

export interface Application {
  id: string;
  package_id: string;
  job_title: string;
  company: string;
  status: ApplicationStatus;
  submitted_at?: string;
  snapshot?: unknown;
  snapshot_sha?: string;
  reminders: ApplicationReminder[];
  history: Array<{ from: string; to: string; at: string; note?: string }>;
  created_at: string;
  updated_at: string;
}

// ── Apply session ──────────────────────────────────────────────────────────
export interface ApplySession {
  id: string;
  package_id: string;
  state: string;
  guardrails: string[];
  allowed_actions: string[];
  history: Array<{ action: string; at: string }>;
  created_at: string;
}

// ── Saved searches ─────────────────────────────────────────────────────────
export type SearchFrequency = "manual" | "daily" | "weekly";

export interface SavedSearch {
  id: string;
  name: string;
  query: string;
  provider?: string;
  location_filter?: string;
  remote_only?: boolean;
  min_salary?: number;
  frequency: SearchFrequency;
  last_run_at?: string;
  created_at: string;
}

// ── Research ───────────────────────────────────────────────────────────────
export type ResearchUsageClass =
  | "background_context"
  | "claim_support"
  | "framing_only"
  | "not_usable";

export type ResearchApprovalStatus =
  | "pending_review"
  | "approved"
  | "rejected";

export interface ResearchFinding {
  id: string;
  query_used: string;
  source_url?: string;
  title?: string;
  snippet: string;
  relevance_score: number;
  usage_class: ResearchUsageClass;
  approval_status: ResearchApprovalStatus;
  approved_at?: string;
  created_at: string;
}

// ── Phase 8: Export tokens ─────────────────────────────────────────────────
export interface ExportToken {
  id: string;
  owner_id: string;
  package_id: string;
  format: string;
  artifact: string;
  include_unsupported: boolean;
  expires_at: string;
  created_at: string;
  used_at: string | null;
}
