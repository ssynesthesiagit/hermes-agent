package com.hermes.gatewayclient;

import android.content.SharedPreferences;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public class SecureGatewayStoreTest {

    private static final String WINDOWS_URL = "http://100.112.52.74:9119";
    private static final String UBUNTU_URL = "http://100.112.52.75:9119";

    @Test
    public void legacyEncryptedUrlLoadsAndMigratesToOneNamedGateway() throws Exception {
        FakePreferences preferences = new FakePreferences();
        SecretKeySpec key = testKey();
        putEncrypted(preferences, key, WINDOWS_URL.getBytes(StandardCharsets.UTF_8));
        SecureGatewayStore store = newStore(preferences, key);

        SecureGatewayStore.GatewayState state = store.getState();

        assertEquals(1, state.getGateways().size());
        assertEquals("100.112.52.74", state.getGateways().get(0).getName());
        assertEquals(WINDOWS_URL, state.getActiveUrl());
        assertEquals(WINDOWS_URL, newStore(preferences, key).getGatewayUrl());
        assertFalse(preferences.getString(SecureGatewayStore.PREF_CIPHERTEXT, "").contains(WINDOWS_URL));
        assertFalse(preferences.getString(SecureGatewayStore.PREF_IV, "").contains(WINDOWS_URL));
    }

    @Test
    public void encryptsMultiGatewayConfigurationWithoutPlaintextPreferences() {
        FakePreferences preferences = new FakePreferences();
        SecureGatewayStore store = newStore(preferences, testKey());

        store.addOrUpdateGateway("Windows", WINDOWS_URL);
        store.addOrUpdateGateway("Ubuntu", UBUNTU_URL);

        SecureGatewayStore.GatewayState state = store.getState();
        assertEquals(2, state.getGateways().size());
        assertEquals("Ubuntu", state.getActiveGateway().getName());
        assertNotNull(preferences.getString(SecureGatewayStore.PREF_CIPHERTEXT, null));
        assertNotNull(preferences.getString(SecureGatewayStore.PREF_IV, null));
        assertNotEquals(WINDOWS_URL, preferences.getString(SecureGatewayStore.PREF_CIPHERTEXT, null));
        assertNotEquals(UBUNTU_URL, preferences.getString(SecureGatewayStore.PREF_CIPHERTEXT, null));
        assertFalse(preferences.getAll().toString().contains("Windows"));
        assertFalse(preferences.getAll().toString().contains("Ubuntu"));
        assertFalse(preferences.getAll().toString().contains(WINDOWS_URL));
        assertFalse(preferences.getAll().toString().contains(UBUNTU_URL));

        try {
            state.getGateways().clear();
            fail("Gateway state must be immutable");
        } catch (UnsupportedOperationException expected) {
            // Expected.
        }
    }

    @Test
    public void activeSwitchPersistsAcrossStoreInstances() {
        FakePreferences preferences = new FakePreferences();
        SecretKeySpec key = testKey();
        SecureGatewayStore store = newStore(preferences, key);
        store.addOrUpdateGateway("Windows", WINDOWS_URL);
        store.addOrUpdateGateway("Ubuntu", UBUNTU_URL);

        store.selectGateway(WINDOWS_URL);

        assertEquals(WINDOWS_URL, newStore(preferences, key).getState().getActiveUrl());
        assertEquals(2, newStore(preferences, key).getState().getGateways().size());
    }

    @Test
    public void updateCurrentDeduplicatesNormalizedUrlAndActivatesIt() {
        FakePreferences preferences = new FakePreferences();
        SecureGatewayStore store = newStore(preferences, testKey());
        store.addOrUpdateGateway("Windows", WINDOWS_URL);
        store.addOrUpdateGateway("Ubuntu", UBUNTU_URL);

        store.updateCurrentGateway("Ubuntu desktop", "HTTP://100.112.52.74:9119/");

        SecureGatewayStore.GatewayState state = store.getState();
        assertEquals(1, state.getGateways().size());
        assertEquals("Ubuntu desktop", state.getActiveGateway().getName());
        assertEquals(WINDOWS_URL, state.getActiveUrl());
    }

    @Test
    public void corruptStructuredPayloadFailsClosedAndPreservesUnrelatedPreferences() throws Exception {
        FakePreferences preferences = new FakePreferences();
        preferences.values.put("unrelated", "preserve me");
        SecretKeySpec key = testKey();

        // Magic + version, but no count or fields: it must not fall back to the
        // legacy URL parser.
        byte[] malformed = new byte[] {'H', 'G', 'W', '1', 0, 0, 0, 1};
        putEncrypted(preferences, key, malformed);

        SecureGatewayStore.GatewayState state = newStore(preferences, key).getState();

        assertTrue(state.isEmpty());
        assertFalse(preferences.values.containsKey(SecureGatewayStore.PREF_CIPHERTEXT));
        assertFalse(preferences.values.containsKey(SecureGatewayStore.PREF_IV));
        assertEquals("preserve me", preferences.getString("unrelated", null));
    }

    @Test
    public void keyFailureClearsOnlyTheUnusableStoredValue() {
        FakePreferences preferences = new FakePreferences();
        preferences.values.put("unrelated", "preserve me");
        preferences.values.put(SecureGatewayStore.PREF_CIPHERTEXT, "not ciphertext");
        preferences.values.put(SecureGatewayStore.PREF_IV, "not iv");
        SecureGatewayStore store = new SecureGatewayStore(preferences, () -> {
            throw new GeneralSecurityException("test key failure");
        });

        assertNull(store.getGatewayUrl());
        assertFalse(preferences.values.containsKey(SecureGatewayStore.PREF_CIPHERTEXT));
        assertFalse(preferences.values.containsKey(SecureGatewayStore.PREF_IV));
        assertEquals("preserve me", preferences.getString("unrelated", null));
    }

    private static SecureGatewayStore newStore(FakePreferences preferences, SecretKeySpec key) {
        return new SecureGatewayStore(preferences, () -> key, new JavaBase64Codec());
    }

    private static SecretKeySpec testKey() {
        return new SecretKeySpec(new byte[] {
            0x10, 0x21, 0x32, 0x43, 0x54, 0x65, 0x76, 0x07,
            0x18, 0x29, 0x3a, 0x4b, 0x5c, 0x6d, 0x7e, 0x0f
        }, "AES");
    }

    private static void putEncrypted(FakePreferences preferences, SecretKeySpec key, byte[] plaintext)
        throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key, new SecureRandom());
        preferences.values.put(
            SecureGatewayStore.PREF_CIPHERTEXT,
            Base64.getEncoder().encodeToString(cipher.doFinal(plaintext))
        );
        preferences.values.put(
            SecureGatewayStore.PREF_IV,
            Base64.getEncoder().encodeToString(cipher.getIV())
        );
    }

    private static final class FakePreferences implements SharedPreferences {
        private final Map<String, Object> values = new HashMap<>();

        @Override
        public Map<String, ?> getAll() {
            return Collections.unmodifiableMap(values);
        }

        @Override
        public String getString(String key, String defaultValue) {
            Object value = values.get(key);
            return value instanceof String ? (String) value : defaultValue;
        }

        @SuppressWarnings("unchecked")
        @Override
        public Set<String> getStringSet(String key, Set<String> defaultValue) {
            Object value = values.get(key);
            return value instanceof Set ? (Set<String>) value : defaultValue;
        }

        @Override
        public int getInt(String key, int defaultValue) {
            Object value = values.get(key);
            return value instanceof Integer ? (Integer) value : defaultValue;
        }

        @Override
        public long getLong(String key, long defaultValue) {
            Object value = values.get(key);
            return value instanceof Long ? (Long) value : defaultValue;
        }

        @Override
        public float getFloat(String key, float defaultValue) {
            Object value = values.get(key);
            return value instanceof Float ? (Float) value : defaultValue;
        }

        @Override
        public boolean getBoolean(String key, boolean defaultValue) {
            Object value = values.get(key);
            return value instanceof Boolean ? (Boolean) value : defaultValue;
        }

        @Override
        public boolean contains(String key) {
            return values.containsKey(key);
        }

        @Override
        public Editor edit() {
            return new Editor() {
                private boolean clear;
                private final Map<String, Object> updates = new HashMap<>();
                private final Set<String> removals = new java.util.HashSet<>();

                @Override
                public Editor putString(String key, String value) {
                    updates.put(key, value);
                    return this;
                }

                @Override
                public Editor putStringSet(String key, Set<String> value) {
                    updates.put(key, value);
                    return this;
                }

                @Override
                public Editor putInt(String key, int value) {
                    updates.put(key, value);
                    return this;
                }

                @Override
                public Editor putLong(String key, long value) {
                    updates.put(key, value);
                    return this;
                }

                @Override
                public Editor putFloat(String key, float value) {
                    updates.put(key, value);
                    return this;
                }

                @Override
                public Editor putBoolean(String key, boolean value) {
                    updates.put(key, value);
                    return this;
                }

                @Override
                public Editor remove(String key) {
                    removals.add(key);
                    return this;
                }

                @Override
                public Editor clear() {
                    clear = true;
                    return this;
                }

                @Override
                public boolean commit() {
                    applyChanges();
                    return true;
                }

                @Override
                public void apply() {
                    applyChanges();
                }

                private void applyChanges() {
                    if (clear) {
                        values.clear();
                    }
                    values.keySet().removeAll(removals);
                    values.putAll(updates);
                }
            };
        }

        @Override
        public void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener) {}

        @Override
        public void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener) {}
    }

    private static final class JavaBase64Codec implements SecureGatewayStore.Base64Codec {
        @Override
        public byte[] decode(String value) {
            return Base64.getDecoder().decode(value);
        }

        @Override
        public String encode(byte[] value) {
            return Base64.getEncoder().encodeToString(value);
        }
    }
}
