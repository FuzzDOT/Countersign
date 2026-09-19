import type { EntityType, RelationType } from '@/api/types';

export const RELATION_TYPES: readonly RelationType[] = [
  'WIRED_FUNDS_TO',
  'OWNED_BY',
  'INVOICED',
  'SHARES_ADDRESS_WITH',
  'SIGNATORY_OF',
  'NO_RELATION',
];

export const ENTITY_TYPES: readonly EntityType[] = [
  'ORG',
  'PERSON',
  'ACCOUNT_REF',
  'MONEY',
  'DATE',
  'TRANSACTION_TYPE',
];

const VERBS: Record<string, string> = {
  WIRED_FUNDS_TO: 'wired funds to',
  OWNED_BY: 'is owned by',
  INVOICED: 'invoiced',
  SHARES_ADDRESS_WITH: 'shares an address with',
  SIGNATORY_OF: 'is signatory of',
  NO_RELATION: 'has no relation to',
};

/** "WIRED_FUNDS_TO" becomes "wired funds to". Unknown relations degrade to a readable form. */
export function relationVerb(relation: string): string {
  return VERBS[relation] ?? relation.toLowerCase().replace(/_/g, ' ');
}

const NOUN_LABELS: Record<string, string> = {
  WIRED_FUNDS_TO: 'Wired funds to',
  OWNED_BY: 'Owned by',
  INVOICED: 'Invoiced',
  SHARES_ADDRESS_WITH: 'Shares address with',
  SIGNATORY_OF: 'Signatory of',
  NO_RELATION: 'No relation',
};

export function relationLabel(relation: string): string {
  return NOUN_LABELS[relation] ?? relationVerb(relation);
}

const ENTITY_LABELS: Record<string, string> = {
  ORG: 'Organisation',
  PERSON: 'Person',
  ACCOUNT_REF: 'Account reference',
  MONEY: 'Amount',
  DATE: 'Date',
  TRANSACTION_TYPE: 'Transaction type',
};

export function entityTypeLabel(type: string): string {
  return ENTITY_LABELS[type] ?? type.toLowerCase().replace(/_/g, ' ');
}

/** How an edge is drawn (brief §9.2): funds are solid, ownership dashed, shared attributes dotted. */
export type RelationKind = 'funds' | 'ownership' | 'shared';

export function relationKind(relation: string): RelationKind {
  switch (relation) {
    case 'OWNED_BY':
    case 'SIGNATORY_OF':
      return 'ownership';
    case 'SHARES_ADDRESS_WITH':
      return 'shared';
    default:
      return 'funds';
  }
}

export function dashFor(kind: RelationKind): string | undefined {
  if (kind === 'ownership') return '7 4';
  if (kind === 'shared') return '1.5 4';
  return undefined;
}
