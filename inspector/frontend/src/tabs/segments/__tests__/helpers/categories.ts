// Hand-pinned list of validation category names used by tests as an
// independent witness against ``../../domain/registry.ts``. Order matches
// the accordion declaration in plan §Appendix A.

export const ALL_CATEGORIES = [
  'failed',
  'missing_verses',
  'missing_words',
  'structural_errors',
  'low_confidence',
  'low_confidence_v2',
  'repetitions',
  'audio_bleeding',
  'boundary_adj',
  'cross_verse',
  'qalqala',
  'muqattaat',
  'basmala_amin',
  'hidden_pause',
  'missed_waqf',
  'false_split',
  'unmarked_wasl',
] as const;

export type CategoryName = typeof ALL_CATEGORIES[number];

export const PER_SEGMENT_CATEGORIES: CategoryName[] = [
  'failed',
  'low_confidence',
  'low_confidence_v2',
  'repetitions',
  'audio_bleeding',
  'boundary_adj',
  'cross_verse',
  'qalqala',
  'muqattaat',
  'basmala_amin',
  'hidden_pause',
  'missed_waqf',
  'false_split',
  'unmarked_wasl',
];

export const CAN_IGNORE_CATEGORIES: CategoryName[] = [
  'low_confidence',
  'low_confidence_v2',
  'repetitions',
  'audio_bleeding',
  'boundary_adj',
  'basmala_amin',
  'hidden_pause',
  'missed_waqf',
  'false_split',
  'unmarked_wasl',
];

export const PERSISTS_IGNORE_CATEGORIES: CategoryName[] = [...CAN_IGNORE_CATEGORIES];

export const AUTO_SUPPRESS_CATEGORIES: CategoryName[] = [
  'failed',
  'missing_verses',
  'structural_errors',
  'low_confidence',
  'low_confidence_v2',
  'repetitions',
  'audio_bleeding',
  'boundary_adj',
  'basmala_amin',
  'hidden_pause',
  'missed_waqf',
  'false_split',
  'unmarked_wasl',
];
