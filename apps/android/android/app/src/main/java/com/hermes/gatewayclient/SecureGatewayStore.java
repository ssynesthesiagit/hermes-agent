package com.hermes.gatewayclient;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.security.KeyStoreException;
import java.security.NoSuchAlgorithmException;
import java.security.cert.CertificateException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Stores the complete saved gateway configuration in one encrypted payload. */
public final class SecureGatewayStore {

    // These names are retained so an upgrade can read the old single-URL store.
    static final String PREF_CIPHERTEXT = "gateway_url_ciphertext";
    static final String PREF_IV = "gateway_url_iv";

    private static final String PREFS_NAME = "hermes_gateway_secure";
    private static final String KEY_ALIAS = "hermes_gateway_aes_gcm";
    private static final int GCM_TAG_BITS = 128;

    private static final byte[] PAYLOAD_MAGIC = new byte[] {'H', 'G', 'W', '1'};
    private static final int PAYLOAD_VERSION = 1;

    static final int MAX_GATEWAYS = 16;
    static final int MAX_LABEL_LENGTH = 64;
    static final int MAX_URL_LENGTH = 2048;
    private static final int MAX_LABEL_BYTES = MAX_LABEL_LENGTH * 4;
    private static final int MAX_PAYLOAD_BYTES = 16 * 1024;
    private static final int MAX_ENCODED_FIELD_LENGTH = 32 * 1024;

    interface KeySource {
        SecretKey getOrCreateKey() throws GeneralSecurityException;
    }

    interface Base64Codec {
        byte[] decode(String value);
        String encode(byte[] value);
    }

    private final SharedPreferences preferences;
    private final KeySource keySource;
    private final Base64Codec base64Codec;

    public SecureGatewayStore(Context context) {
        this(
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE),
            new AndroidKeyStoreSource(),
            new AndroidBase64Codec()
        );
    }

    SecureGatewayStore(SharedPreferences preferences, KeySource keySource) {
        this(preferences, keySource, new AndroidBase64Codec());
    }

    SecureGatewayStore(SharedPreferences preferences, KeySource keySource, Base64Codec base64Codec) {
        this.preferences = preferences;
        this.keySource = keySource;
        this.base64Codec = base64Codec;
    }

    /** Returns a defensive immutable view of the saved configuration. */
    public GatewayState getState() {
        try {
            String ciphertext = preferences.getString(PREF_CIPHERTEXT, null);
            String iv = preferences.getString(PREF_IV, null);
            if (ciphertext == null || iv == null) {
                if (ciphertext != null || iv != null) {
                    clearStoredValue();
                }
                return GatewayState.empty();
            }

            ParsedPayload parsed = parsePayload(decrypt(ciphertext, iv, keySource.getOrCreateKey(), base64Codec));
            if (parsed.legacy) {
                // The old format encrypted one raw URL. Rewrite it immediately into
                // the structured format so future reads do not depend on migration.
                persistState(parsed.state);
            }
            return parsed.state;
        } catch (Exception ex) {
            // Stale keys, tampering, malformed framing, and invalid UTF-8 all fail
            // closed. Only this store's two preference keys are removed.
            clearStoredValue();
            return GatewayState.empty();
        }
    }

    /** Backward-compatible accessor for callers that only need the active URL. */
    public String getGatewayUrl() {
        return getState().getActiveUrl();
    }

    /** Adds or updates a named gateway and makes it active. */
    public void addOrUpdateGateway(String name, String gatewayUrl) {
        String normalizedUrl = normalizeUrl(gatewayUrl);
        String normalizedName = normalizeLabel(name, normalizedUrl);
        GatewayState current = getState();

        LinkedHashMap<String, Gateway> gateways = new LinkedHashMap<>();
        for (Gateway gateway : current.gateways) {
            gateways.put(gateway.url, gateway);
        }
        gateways.put(normalizedUrl, new Gateway(normalizedName, normalizedUrl));
        persistState(new GatewayState(new ArrayList<>(gateways.values()), normalizedUrl));
    }

    /** Selects an already saved gateway and persists it as the active entry. */
    public void selectGateway(String gatewayUrl) {
        String normalizedUrl = normalizeUrl(gatewayUrl);
        GatewayState current = getState();
        if (!current.containsUrl(normalizedUrl)) {
            throw new SecureStoreException("Gateway is not saved");
        }
        persistState(new GatewayState(current.gateways, normalizedUrl));
    }

    /** Updates the active entry, deduplicating it if its URL now matches another entry. */
    public void updateCurrentGateway(String name, String gatewayUrl) {
        GatewayState current = getState();
        if (current.activeUrl == null) {
            throw new SecureStoreException("There is no active gateway");
        }

        String normalizedUrl = normalizeUrl(gatewayUrl);
        String normalizedName = normalizeLabel(name, normalizedUrl);
        LinkedHashMap<String, Gateway> gateways = new LinkedHashMap<>();
        for (Gateway gateway : current.gateways) {
            if (!gateway.url.equals(current.activeUrl) && !gateway.url.equals(normalizedUrl)) {
                gateways.put(gateway.url, gateway);
            }
        }
        gateways.put(normalizedUrl, new Gateway(normalizedName, normalizedUrl));
        persistState(new GatewayState(new ArrayList<>(gateways.values()), normalizedUrl));
    }

    /** Convenience alias for the add/update operation. */
    public void saveGateway(String name, String gatewayUrl) {
        addOrUpdateGateway(name, gatewayUrl);
    }

    /** Backward-compatible single-URL save operation. */
    public void saveGatewayUrl(String gatewayUrl) {
        String normalizedUrl = normalizeUrl(gatewayUrl);
        addOrUpdateGateway(defaultLabelForUrl(normalizedUrl), normalizedUrl);
    }

    public void clearGatewayUrl() {
        clearStoredValue();
    }

    public void clearGateways() {
        clearStoredValue();
    }

    private ParsedPayload parsePayload(byte[] plaintext) throws IOException {
        if (plaintext == null || plaintext.length == 0 || plaintext.length > MAX_PAYLOAD_BYTES) {
            throw new IOException("Invalid gateway payload size");
        }
        if (!hasPayloadMagic(plaintext)) {
            String legacyUrl = decodeUtf8(plaintext);
            String normalizedUrl = normalizeUrl(legacyUrl);
            Gateway gateway = new Gateway(defaultLabelForUrl(normalizedUrl), normalizedUrl);
            return new ParsedPayload(new GatewayState(Collections.singletonList(gateway), normalizedUrl), true);
        }

        DataInputStream input = new DataInputStream(new ByteArrayInputStream(plaintext));
        for (byte expected : PAYLOAD_MAGIC) {
            if (input.readByte() != expected) {
                throw new IOException("Invalid gateway payload magic");
            }
        }

        int version = input.readInt();
        if (version != PAYLOAD_VERSION) {
            throw new IOException("Unsupported gateway payload version");
        }
        int count = input.readInt();
        if (count < 1 || count > MAX_GATEWAYS) {
            throw new IOException("Invalid gateway count");
        }

        String activeUrl = normalizeUrl(readString(input, MAX_URL_LENGTH, MAX_URL_LENGTH));
        LinkedHashMap<String, Gateway> gateways = new LinkedHashMap<>();
        for (int index = 0; index < count; index++) {
            String name = readString(input, MAX_LABEL_LENGTH, MAX_LABEL_BYTES);
            String url = normalizeUrl(readString(input, MAX_URL_LENGTH, MAX_URL_LENGTH));
            gateways.put(url, new Gateway(normalizeLabel(name, url), url));
        }

        if (input.available() != 0 || !gateways.containsKey(activeUrl)) {
            throw new IOException("Invalid gateway payload framing");
        }
        return new ParsedPayload(new GatewayState(new ArrayList<>(gateways.values()), activeUrl), false);
    }

    private void persistState(GatewayState state) {
        if (state == null || state.activeUrl == null || state.gateways.isEmpty()) {
            throw new SecureStoreException("Gateway state is empty");
        }

        try {
            byte[] payload = serializeState(state);
            EncryptedValue encrypted = encrypt(payload, keySource.getOrCreateKey(), base64Codec);
            if (encrypted.ciphertext.length() > MAX_ENCODED_FIELD_LENGTH
                || encrypted.iv.length() > MAX_ENCODED_FIELD_LENGTH) {
                throw new GeneralSecurityException("Encrypted gateway payload is too large");
            }
            boolean committed = preferences
                .edit()
                .putString(PREF_CIPHERTEXT, encrypted.ciphertext)
                .putString(PREF_IV, encrypted.iv)
                .commit();
            if (!committed) {
                throw new GeneralSecurityException("Unable to commit gateway payload");
            }
        } catch (Exception ex) {
            throw new SecureStoreException("Unable to protect gateway configuration", ex);
        }
    }

    private static byte[] serializeState(GatewayState state) throws IOException {
        if (state.gateways.size() < 1 || state.gateways.size() > MAX_GATEWAYS) {
            throw new IOException("Invalid gateway count");
        }

        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        DataOutputStream output = new DataOutputStream(bytes);
        output.write(PAYLOAD_MAGIC);
        output.writeInt(PAYLOAD_VERSION);
        output.writeInt(state.gateways.size());
        writeString(output, state.activeUrl, MAX_URL_LENGTH, MAX_URL_LENGTH);
        for (Gateway gateway : state.gateways) {
            writeString(output, gateway.name, MAX_LABEL_LENGTH, MAX_LABEL_BYTES);
            writeString(output, gateway.url, MAX_URL_LENGTH, MAX_URL_LENGTH);
        }
        output.flush();
        byte[] payload = bytes.toByteArray();
        if (payload.length > MAX_PAYLOAD_BYTES) {
            throw new IOException("Gateway payload is too large");
        }
        return payload;
    }

    private static void writeString(DataOutputStream output, String value, int maxCharacters, int maxBytes)
        throws IOException {
        if (value == null || value.length() > maxCharacters) {
            throw new IOException("Gateway field is too long");
        }
        byte[] encoded = value.getBytes(StandardCharsets.UTF_8);
        if (encoded.length > maxBytes) {
            throw new IOException("Gateway field is too large");
        }
        output.writeInt(encoded.length);
        output.write(encoded);
    }

    private static String readString(DataInputStream input, int maxCharacters, int maxBytes)
        throws IOException {
        int length;
        try {
            length = input.readInt();
        } catch (EOFException ex) {
            throw new IOException("Truncated gateway field", ex);
        }
        if (length < 0 || length > maxBytes || length > input.available()) {
            throw new IOException("Invalid gateway field length");
        }

        byte[] encoded = new byte[length];
        input.readFully(encoded);
        String value = decodeUtf8(encoded);
        if (value.length() > maxCharacters) {
            throw new IOException("Gateway field is too long");
        }
        return value;
    }

    private static String decodeUtf8(byte[] value) throws IOException {
        try {
            return StandardCharsets.UTF_8
                .newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(value))
                .toString();
        } catch (CharacterCodingException ex) {
            throw new IOException("Invalid UTF-8 gateway payload", ex);
        }
    }

    private static String normalizeUrl(String value) {
        if (value == null || value.isEmpty() || value.length() > MAX_URL_LENGTH || !value.equals(value.trim())) {
            throw new SecureStoreException("Gateway URL is invalid");
        }
        for (int index = 0; index < value.length(); index++) {
            if (Character.isISOControl(value.charAt(index))) {
                throw new SecureStoreException("Gateway URL is invalid");
            }
        }

        final URI uri;
        try {
            uri = new URI(value);
        } catch (URISyntaxException ex) {
            throw new SecureStoreException("Gateway URL is invalid", ex);
        }

        String scheme = uri.getScheme();
        String authority = uri.getRawAuthority();
        if (scheme == null
            || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))
            || authority == null
            || authority.isEmpty()
            || uri.getUserInfo() != null
            || uri.getRawUserInfo() != null
            || authority.indexOf('@') >= 0
            || authority.indexOf('%') >= 0
            || authority.endsWith(":")) {
            throw new SecureStoreException("Gateway URL is invalid");
        }

        String path = uri.getRawPath();
        if (path == null) {
            path = "";
        }
        if (path.equals("/")) {
            path = "";
        }
        while (path.length() > 1 && path.endsWith("/")) {
            path = path.substring(0, path.length() - 1);
        }

        StringBuilder normalized = new StringBuilder(value.length());
        normalized.append(scheme.toLowerCase(Locale.ROOT));
        normalized.append("://");
        normalized.append(authority.toLowerCase(Locale.ROOT));
        normalized.append(path);
        if (uri.getRawQuery() != null) {
            normalized.append('?').append(uri.getRawQuery());
        }
        if (uri.getRawFragment() != null) {
            normalized.append('#').append(uri.getRawFragment());
        }
        String result = normalized.toString();
        if (result.length() > MAX_URL_LENGTH) {
            throw new SecureStoreException("Gateway URL is too long");
        }
        return result;
    }

    private static String normalizeLabel(String value, String url) {
        String label = value == null ? "" : value.trim();
        if (label.isEmpty()) {
            label = defaultLabelForUrl(url);
        }
        if (label.isEmpty() || label.length() > MAX_LABEL_LENGTH) {
            throw new SecureStoreException("Gateway name is invalid");
        }
        for (int index = 0; index < label.length(); index++) {
            if (Character.isISOControl(label.charAt(index))) {
                throw new SecureStoreException("Gateway name is invalid");
            }
        }
        return label;
    }

    static String defaultLabelForUrl(String value) {
        try {
            URI uri = new URI(value);
            String host = uri.getHost();
            if (host == null || host.isEmpty()) {
                String authority = uri.getRawAuthority();
                if (authority != null && authority.startsWith("[")) {
                    int close = authority.indexOf(']');
                    host = close > 1 ? authority.substring(1, close) : authority;
                } else if (authority != null) {
                    int colon = authority.indexOf(':');
                    host = colon > 0 ? authority.substring(0, colon) : authority;
                }
            }
            if (host != null && !host.isEmpty()) {
                return host;
            }
        } catch (URISyntaxException ignored) {
            // The caller validates the URL separately; use a safe fallback here.
        }
        return "Gateway";
    }

    private static boolean hasPayloadMagic(byte[] plaintext) {
        if (plaintext.length < PAYLOAD_MAGIC.length) {
            return false;
        }
        for (int index = 0; index < PAYLOAD_MAGIC.length; index++) {
            if (plaintext[index] != PAYLOAD_MAGIC[index]) {
                return false;
            }
        }
        return true;
    }

    private static byte[] decrypt(
        String encodedCiphertext,
        String encodedIv,
        SecretKey key,
        Base64Codec base64Codec
    ) throws GeneralSecurityException {
        if (encodedCiphertext == null
            || encodedIv == null
            || encodedCiphertext.isEmpty()
            || encodedIv.isEmpty()
            || encodedCiphertext.length() > MAX_ENCODED_FIELD_LENGTH
            || encodedIv.length() > MAX_ENCODED_FIELD_LENGTH) {
            throw new GeneralSecurityException("Invalid encrypted gateway payload");
        }

        byte[] ciphertext = base64Codec.decode(encodedCiphertext);
        byte[] iv = base64Codec.decode(encodedIv);
        if (ciphertext == null
            || iv == null
            || ciphertext.length < (GCM_TAG_BITS / 8)
            || ciphertext.length > MAX_PAYLOAD_BYTES + (GCM_TAG_BITS / 8)
            || iv.length < 12
            || iv.length > 32) {
            throw new GeneralSecurityException("Invalid encrypted gateway payload");
        }

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_BITS, iv));
        byte[] plaintext = cipher.doFinal(ciphertext);
        if (plaintext.length == 0 || plaintext.length > MAX_PAYLOAD_BYTES) {
            throw new GeneralSecurityException("Invalid gateway payload size");
        }
        return plaintext;
    }

    private static EncryptedValue encrypt(byte[] value, SecretKey key, Base64Codec base64Codec)
        throws GeneralSecurityException {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key);
        byte[] ciphertext = cipher.doFinal(value);
        return new EncryptedValue(
            base64Codec.encode(ciphertext),
            base64Codec.encode(cipher.getIV())
        );
    }

    private void clearStoredValue() {
        preferences.edit().remove(PREF_CIPHERTEXT).remove(PREF_IV).apply();
    }

    private static final class ParsedPayload {
        private final GatewayState state;
        private final boolean legacy;

        private ParsedPayload(GatewayState state, boolean legacy) {
            this.state = state;
            this.legacy = legacy;
        }
    }

    private static final class EncryptedValue {
        private final String ciphertext;
        private final String iv;

        private EncryptedValue(String ciphertext, String iv) {
            this.ciphertext = ciphertext;
            this.iv = iv;
        }
    }

    public static final class Gateway {
        private final String name;
        private final String url;

        private Gateway(String name, String url) {
            this.name = name;
            this.url = url;
        }

        public String getName() {
            return name;
        }

        public String getLabel() {
            return name;
        }

        public String getUrl() {
            return url;
        }

        public String getGatewayUrl() {
            return url;
        }
    }

    public static final class GatewayState {
        private final List<Gateway> gateways;
        private final String activeUrl;

        private GatewayState(List<Gateway> gateways, String activeUrl) {
            this.gateways = Collections.unmodifiableList(new ArrayList<>(gateways));
            this.activeUrl = activeUrl;
        }

        static GatewayState empty() {
            return new GatewayState(Collections.emptyList(), null);
        }

        public List<Gateway> getGateways() {
            return Collections.unmodifiableList(new ArrayList<>(gateways));
        }

        public String getActiveUrl() {
            return activeUrl;
        }

        public Gateway getActiveGateway() {
            if (activeUrl == null) {
                return null;
            }
            for (Gateway gateway : gateways) {
                if (gateway.url.equals(activeUrl)) {
                    return gateway;
                }
            }
            return null;
        }

        public boolean isEmpty() {
            return gateways.isEmpty();
        }

        private boolean containsUrl(String url) {
            for (Gateway gateway : gateways) {
                if (gateway.url.equals(url)) {
                    return true;
                }
            }
            return false;
        }
    }

    public static final class SecureStoreException extends RuntimeException {
        private SecureStoreException(String message) {
            super(message);
        }

        private SecureStoreException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private static final class AndroidBase64Codec implements Base64Codec {
        @Override
        public byte[] decode(String value) {
            return Base64.decode(value, Base64.DEFAULT);
        }

        @Override
        public String encode(byte[] value) {
            return Base64.encodeToString(value, Base64.NO_WRAP);
        }
    }

    private static final class AndroidKeyStoreSource implements KeySource {
        @Override
        public SecretKey getOrCreateKey() throws GeneralSecurityException {
            try {
                KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
                keyStore.load(null);
                java.security.Key existing = keyStore.getKey(KEY_ALIAS, null);
                if (existing instanceof SecretKey) {
                    return (SecretKey) existing;
                }
                if (existing != null) {
                    keyStore.deleteEntry(KEY_ALIAS);
                }

                KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
                generator.init(
                    new KeyGenParameterSpec.Builder(
                        KEY_ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
                    )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setRandomizedEncryptionRequired(true)
                        .build()
                );
                return generator.generateKey();
            } catch (CertificateException | NoSuchAlgorithmException | KeyStoreException ex) {
                throw new GeneralSecurityException("Android Keystore unavailable", ex);
            } catch (java.io.IOException ex) {
                throw new GeneralSecurityException("Android Keystore unavailable", ex);
            }
        }
    }
}
