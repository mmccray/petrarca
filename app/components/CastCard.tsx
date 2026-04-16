import React, { useRef, useState } from 'react';
import { Animated, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, layout } from '../design/tokens';

interface CastPosition {
  position_id: string;
  position: number;
  question_text: string;
  answer_text: string; // person name
  question_variants: {
    role: string;
    significance: string;
    entity_id?: string;
  };
  stability_days: number;
  due_at: number;
  review_count?: number;
  last_score?: string;
}

export interface CastCardData {
  card_type: 'cast';
  card_id: string;
  title: string;
  description: string; // event_context
  domain_id: string;
  domain_title: string;
  node_id: string;
  positions: CastPosition[];
  provenance?: {
    origin: string;
    review_count: number;
  };
}

interface PositionResult {
  position_id: string;
  score: 'knew' | 'missed';
  reveal_time_ms: number;
}

interface Props {
  card: CastCardData;
  onComplete: (results: PositionResult[]) => void;
}

type PositionState = 'anchor' | 'hidden' | 'revealed' | 'knew' | 'missed';

export default function CastCard({ card, onComplete }: Props) {
  const positions = [...card.positions].sort((a, b) => a.position - b.position);

  // Pick 2-3 blanks by urgency (most-due positions)
  const blankIds = new Set<string>();
  const sortedByUrgency = [...positions].sort((a, b) => {
    const aDue = a.due_at || 0;
    const bDue = b.due_at || 0;
    return aDue - bDue; // most overdue first
  });
  const numBlanks = Math.min(positions.length <= 4 ? 2 : 3, positions.length - 1);
  for (const p of sortedByUrgency) {
    if (blankIds.size >= numBlanks) break;
    blankIds.add(p.position_id);
  }

  const [states, setStates] = useState<Record<string, PositionState>>(() => {
    const init: Record<string, PositionState> = {};
    for (const p of positions) {
      init[p.position_id] = blankIds.has(p.position_id) ? 'hidden' : 'anchor';
    }
    return init;
  });
  const [revealTimes, setRevealTimes] = useState<Record<string, number>>({});
  const fadeAnims = useRef<Record<string, Animated.Value>>({}).current;

  for (const p of positions) {
    if (!fadeAnims[p.position_id]) {
      fadeAnims[p.position_id] = new Animated.Value(0);
    }
  }

  const domainLabel = (card.domain_title || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
    .slice(0, 40);

  const blanks = positions.filter(p => blankIds.has(p.position_id));
  const allBlanksGraded = blanks.every(
    p => states[p.position_id] === 'knew' || states[p.position_id] === 'missed',
  );
  const knewCount = blanks.filter(p => states[p.position_id] === 'knew').length;

  const animateReveal = (positionId: string) => {
    fadeAnims[positionId].setValue(0);
    Animated.timing(fadeAnims[positionId], {
      toValue: 1,
      duration: 250,
      useNativeDriver: true,
    }).start();
  };

  const revealOne = (positionId: string) => {
    setStates(prev => ({ ...prev, [positionId]: 'revealed' }));
    setRevealTimes(prev => ({ ...prev, [positionId]: Date.now() }));
    animateReveal(positionId);
  };

  const grade = (positionId: string, score: 'knew' | 'missed') => {
    setStates(prev => ({ ...prev, [positionId]: score }));
  };

  const handleContinue = () => {
    const now = Date.now();
    const results: PositionResult[] = blanks.map(p => ({
      position_id: p.position_id,
      score: states[p.position_id] === 'knew' ? 'knew' as const : 'missed' as const,
      reveal_time_ms: revealTimes[p.position_id]
        ? now - revealTimes[p.position_id]
        : 0,
    }));
    onComplete(results);
  };

  return (
    <View style={st.card}>
      {/* Header */}
      <View style={st.headerRow}>
        <View style={st.badge}>
          <Text style={st.badgeText}>{'\u2694'} Cast</Text>
        </View>
        <Text style={st.domainLabel} numberOfLines={1}>{domainLabel}</Text>
      </View>

      {/* Title + context */}
      <Text style={st.title}>{card.title}</Text>
      {card.description ? (
        <Text style={st.eventContext}>{card.description}</Text>
      ) : null}

      {/* Cast list */}
      {positions.map((p, i) => {
        const state = states[p.position_id];
        const qv = p.question_variants || { role: '', significance: '' };
        const isAnchor = state === 'anchor';

        return (
          <View key={p.position_id} style={[st.positionRow, i > 0 && st.positionBorder]}>
            {isAnchor ? (
              // Anchor: shown with name + role (dimmed)
              <View style={st.anchorRow}>
                <Text style={st.anchorName}>{p.answer_text}</Text>
                <Text style={st.anchorRole}>{qv.role}</Text>
              </View>
            ) : state === 'hidden' ? (
              // Blank: show question as prompt
              <View>
                <Text style={st.blankLabel}>???</Text>
                <Text style={st.questionText}>{p.question_text}</Text>
                <Pressable style={st.revealBtn} onPress={() => revealOne(p.position_id)}>
                  <Text style={st.revealBtnText}>Reveal</Text>
                </Pressable>
              </View>
            ) : (
              // Revealed: show name + role + significance + grade buttons
              <Animated.View style={{ opacity: fadeAnims[p.position_id] }}>
                <Text style={st.revealedName}>{p.answer_text}</Text>
                <Text style={st.revealedRole}>{qv.role}</Text>
                {qv.significance ? (
                  <Text style={st.significance}>{qv.significance}</Text>
                ) : null}

                {state === 'revealed' && (
                  <View style={st.gradeRow}>
                    <Pressable
                      style={st.knewBtn}
                      onPress={() => grade(p.position_id, 'knew')}
                    >
                      <Text style={st.knewBtnText}>Knew</Text>
                    </Pressable>
                    <Pressable
                      style={st.missedBtn}
                      onPress={() => grade(p.position_id, 'missed')}
                    >
                      <Text style={st.missedBtnText}>Missed</Text>
                    </Pressable>
                  </View>
                )}

                {state === 'knew' && (
                  <Text style={st.gradeLabel}>{'\u2713'} Knew</Text>
                )}
                {state === 'missed' && (
                  <Text style={st.gradeLabelMissed}>{'\u2717'} Missed</Text>
                )}
              </Animated.View>
            )}
          </View>
        );
      })}

      {/* Summary */}
      {allBlanksGraded && (
        <View style={st.summaryRow}>
          <Text style={st.summaryText}>
            {knewCount}/{blanks.length} identified
          </Text>
        </View>
      )}

      {/* Continue */}
      {allBlanksGraded && (
        <Pressable style={st.continueBtn} onPress={handleContinue}>
          <Text style={st.continueBtnText}>Continue {'\u2192'}</Text>
        </Pressable>
      )}
    </View>
  );
}

const webBold500 = Platform.OS === 'web' ? { fontWeight: '500' as const } : {};
const webBold600 = Platform.OS === 'web' ? { fontWeight: '600' as const } : {};
const webItalic = Platform.OS === 'web' ? { fontStyle: 'italic' as const } : {};

const st = StyleSheet.create({
  card: { marginHorizontal: layout.screenPadding, marginBottom: 20, paddingVertical: 16, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.rule },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  badge: { backgroundColor: '#6B4C8A', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 2 },
  badgeText: { fontFamily: fonts.uiMedium, fontSize: 10, color: colors.parchment, textTransform: 'uppercase', letterSpacing: 0.5, ...webBold500 },
  domainLabel: { fontFamily: fonts.ui, fontSize: 11, color: colors.textMuted, flex: 1 },
  title: { fontFamily: fonts.displaySemiBold, fontSize: 20, lineHeight: 26, color: colors.ink, marginBottom: 4, ...webBold600 },
  eventContext: { fontFamily: fonts.readingItalic, fontSize: 14, lineHeight: 20, color: colors.textSecondary, marginBottom: 16, ...webItalic },
  positionRow: { paddingVertical: 10 },
  positionBorder: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.rule },
  // Anchor (shown, dimmed)
  anchorRow: { opacity: 0.6 },
  anchorName: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.ink, marginBottom: 2, ...webBold500 },
  anchorRole: { fontFamily: fonts.reading, fontSize: 13, lineHeight: 18, color: colors.textSecondary },
  // Blank (hidden)
  blankLabel: { fontFamily: fonts.bodyMedium, fontSize: 16, color: colors.rubric, marginBottom: 4, ...webBold500 },
  questionText: { fontFamily: fonts.reading, fontSize: 15, lineHeight: 22, color: colors.ink, marginBottom: 8 },
  revealBtn: { alignSelf: 'flex-start', paddingVertical: 6, paddingHorizontal: 14, borderWidth: 1, borderColor: colors.rubric, borderRadius: 4 },
  revealBtnText: { fontFamily: fonts.ui, fontSize: 12, color: colors.rubric },
  // Revealed
  revealedName: { fontFamily: fonts.bodyMedium, fontSize: 16, color: colors.ink, marginBottom: 2, borderLeftWidth: 3, borderLeftColor: '#6B4C8A', paddingLeft: 10, ...webBold500 },
  revealedRole: { fontFamily: fonts.reading, fontSize: 14, lineHeight: 20, color: colors.textBody, paddingLeft: 13, marginBottom: 4 },
  significance: { fontFamily: fonts.readingItalic, fontSize: 13, lineHeight: 18, color: colors.textSecondary, paddingLeft: 13, marginBottom: 8, ...webItalic },
  gradeRow: { flexDirection: 'row', gap: 8, paddingLeft: 13 },
  knewBtn: { flex: 1, paddingVertical: 10, borderRadius: 4, alignItems: 'center', borderWidth: 1, borderColor: colors.claimNew, backgroundColor: 'rgba(42,122,74,0.05)' },
  knewBtnText: { fontFamily: fonts.ui, fontSize: 12, color: colors.claimNew },
  missedBtn: { flex: 1, paddingVertical: 10, borderRadius: 4, alignItems: 'center', borderWidth: 1, borderColor: colors.rubric, backgroundColor: 'rgba(139,37,0,0.05)' },
  missedBtnText: { fontFamily: fonts.ui, fontSize: 12, color: colors.rubric },
  gradeLabel: { fontFamily: fonts.ui, fontSize: 12, color: colors.claimNew, paddingLeft: 13, marginTop: 4 },
  gradeLabelMissed: { fontFamily: fonts.ui, fontSize: 12, color: colors.rubric, paddingLeft: 13, marginTop: 4 },
  summaryRow: { marginTop: 12, paddingTop: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.rule },
  summaryText: { fontFamily: fonts.readingItalic, fontSize: 13, color: colors.textSecondary, ...webItalic },
  continueBtn: { marginTop: 16, paddingVertical: 12, borderWidth: 1, borderColor: '#6B4C8A', borderRadius: 4, alignItems: 'center', backgroundColor: 'rgba(107,76,138,0.04)' },
  continueBtnText: { fontFamily: fonts.body, fontSize: 14, color: '#6B4C8A' },
});
