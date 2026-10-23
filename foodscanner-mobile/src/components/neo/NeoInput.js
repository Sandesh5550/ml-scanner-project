import React, { useState } from 'react';
import { View, Text, TextInput, StyleSheet } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function NeoInput({
  label,
  value,
  onChangeText,
  placeholder,
  rightElement,
  style,
  inputStyle,
  containerStyle,
  error,
  ...rest
}) {
  const [isFocused, setIsFocused] = useState(false);

  return (
    <View style={[styles.wrapper, containerStyle]}>
      {label ? <Text style={styles.label}>{label}</Text> : null}
      <View
        style={[
          styles.inputBox,
          isFocused ? styles.inputBoxFocused : null,
          error ? styles.inputBoxError : null,
          style,
        ]}
      >
        <TextInput
          value={value}
          onChangeText={onChangeText}
          placeholder={placeholder}
          placeholderTextColor={PREMIUM_COLORS.muted}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          selectionColor="#557A3E"
          underlineColorAndroid="transparent"
          style={[styles.input, inputStyle]}
          {...rest}
        />
        {rightElement ? <View style={styles.rightBox}>{rightElement}</View> : null}
      </View>
      {error ? <Text style={styles.errorText}>{error}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    marginBottom: 14,
  },
  label: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginBottom: 6,
    letterSpacing: 0.1,
  },
  inputBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    borderWidth: 1.5,
    borderColor: '#E1E6DC',
    borderRadius: PREMIUM_RADIUS.md, // 16px
    paddingHorizontal: 16,
    ...PREMIUM_SHADOWS.sm,
  },
  inputBoxFocused: {
    borderColor: '#557A3E',
    backgroundColor: '#F7FAF1',
    shadowColor: '#557A3E',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.12,
    shadowRadius: 6,
    elevation: 2,
  },
  inputBoxError: {
    borderColor: PREMIUM_COLORS.status.avoid,
  },
  input: {
    flex: 1,
    paddingVertical: 12,
    fontSize: 16,
    fontWeight: '500',
    color: '#171A17',
    backgroundColor: 'transparent',
    borderWidth: 0,
    outlineStyle: 'none',
    outlineWidth: 0,
  },
  rightBox: {
    marginLeft: 8,
  },
  errorText: {
    marginTop: 5,
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.status.avoid,
  },
});
