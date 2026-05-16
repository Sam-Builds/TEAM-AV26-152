using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;
using FirebaseAdmin;
using FirebaseAdmin.Messaging;
using Google.Apis.Auth.OAuth2;

var builder = WebApplication.CreateBuilder(args);

// ─── Configuration & Variables ──────────────────────────────────────
var connStr = builder.Configuration.GetConnectionString("Default") ?? "Data Source=app.db";
var textBeeBaseUrl = builder.Configuration["TEXTBEE_BASE_URL"] ?? "https://api.textbee.dev/api/v1";
var textBeeApiKey = builder.Configuration["TEXTBEE_API_KEY"];
var textBeeDeviceId = builder.Configuration["TEXTBEE_DEVICE_ID"];
var pythonWorkerUrl = builder.Configuration["PYTHON_WORKER_URL"] ?? "http://localhost:5055";

// ─── Firebase Initialization ──────────────────────────────────────────
#pragma warning disable CS0618
if (FirebaseApp.DefaultInstance == null)
{
    FirebaseApp.Create(new AppOptions()
    {
        Credential = GoogleCredential.FromFile("service-account.json"),
    });
}
#pragma warning restore CS0618

// ─── Services ────────────────────────────────────────────────────────
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();
builder.Services.AddCors(o =>
    o.AddDefaultPolicy(p => p.AllowAnyOrigin().AllowAnyMethod().AllowAnyHeader()));
builder.Services.AddHttpClient();

// ─── Database Migration/Startup ──────────────────────────────────────
using (var initConn = new SqliteConnection(connStr))
{
    initConn.Open();
    var initCmd = initConn.CreateCommand();
    initCmd.CommandText = @"
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT NOT NULL UNIQUE,
            email        TEXT NOT NULL UNIQUE,
            phone        TEXT NOT NULL,
            password     TEXT NOT NULL,
            fcm_token    TEXT, 
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS location (
            user_id       INTEGER PRIMARY KEY,
            home_name     TEXT,
            street_name   TEXT,
            district_name TEXT,
            state_name    TEXT,
            pin_code      INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS disasters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            type         TEXT NOT NULL,
            severity     REAL NOT NULL DEFAULT 0.0,
            latitude     REAL,
            longitude    REAL,
            description  TEXT,
            source       TEXT,
            status       TEXT NOT NULL DEFAULT 'active',
            created_at   TEXT DEFAULT (datetime('now')),
            updated_at   TEXT DEFAULT (datetime('now'))
        );";
    initCmd.ExecuteNonQuery();

    // Ensure fcm_token column exists if table was created in an older version
    var addColCmd = initConn.CreateCommand();
    addColCmd.CommandText = "ALTER TABLE users ADD COLUMN fcm_token TEXT";
    try { addColCmd.ExecuteNonQuery(); } catch { /* column exists */ }
}

// ─── State ──────────────────────────────────────────────────────────
var otpStore = new ConcurrentDictionary<string, OtpEntry>();

var app = builder.Build();

app.UseSwagger();
app.UseSwaggerUI();
app.UseCors();

// ─── Auth Endpoints ──────────────────────────────────────────────────

app.MapPost("/auth/register", async ([FromBody] Dictionary<string, string> body) =>
{
    if (!body.TryGetValue("username", out var username) || !body.TryGetValue("email", out var email) || 
        !body.TryGetValue("phone", out var phone) || !body.TryGetValue("password", out var password))
        return Results.BadRequest(new { success = false, message = "Missing required fields." });

    body.TryGetValue("fcm_token", out var fcmToken);

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var insert = conn.CreateCommand();
    insert.CommandText = "INSERT INTO users (username, email, phone, password, fcm_token) VALUES ($u, $e, $ph, $p, $fcm) RETURNING id, username;";
    insert.Parameters.AddWithValue("$u", username.Trim());
    insert.Parameters.AddWithValue("$e", email.Trim().ToLower());
    insert.Parameters.AddWithValue("$ph", phone.Trim());
    insert.Parameters.AddWithValue("$p", password);
    insert.Parameters.AddWithValue("$fcm", (object?)fcmToken ?? DBNull.Value);

    try {
        using var reader = await insert.ExecuteReaderAsync();
        await reader.ReadAsync();
        return Results.Ok(new { success = true, data = new { id = reader.GetInt64(0), username = reader.GetString(1) } });
    } catch { return Results.Conflict(new { success = false, message = "User already exists." }); }
});
app.MapPost("/auth/login", async ([FromBody] Dictionary<string, string> body) =>
{
    // Validate required fields
    if (!body.TryGetValue("email", out var email) || !body.TryGetValue("password", out var password))
        return Results.BadRequest(new { success = false, message = "Missing email or password." });

    // Normalize email
    var normalizedEmail = email.Trim().ToLower();

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();
    var cmd = conn.CreateCommand();
    cmd.CommandText = "SELECT id, username, email FROM users WHERE email = $email AND password = $pass LIMIT 1";
    cmd.Parameters.AddWithValue("$email", normalizedEmail);
    cmd.Parameters.AddWithValue("$pass", password);
    using var reader = await cmd.ExecuteReaderAsync();
    if (await reader.ReadAsync())
    {
        return Results.Ok(new { success = true, data = new { id = reader.GetInt64(0), username = reader.GetString(1), email = reader.GetString(2) } });
    }
    else
    {
        return Results.Json(new { success = false, message = "Invalid credentials." }, statusCode: 401);
    }
});
app.MapPost("/auth/update-fcm", async ([FromBody] JsonElement body) =>
{
    if (!TryGetFlexibleString(body, out var userIdValue, "user_id", "userId", "id") || !int.TryParse(userIdValue, out var userId) || userId <= 0)
        return Results.BadRequest(new { success = false, message = "Valid user id required." });

    if (!TryGetFlexibleString(body, out var fcmToken, "fcm_token", "fcmToken", "token") || string.IsNullOrWhiteSpace(fcmToken))
        return Results.BadRequest(new { success = false, message = "FCM token required." });

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();
    var cmd = conn.CreateCommand();
    cmd.CommandText = "UPDATE users SET fcm_token = @token WHERE id = @id";
    cmd.Parameters.AddWithValue("@token", fcmToken.Trim());
    cmd.Parameters.AddWithValue("@id", userId);
    var updated = await cmd.ExecuteNonQueryAsync();
    if (updated == 0)
        return Results.NotFound(new { success = false, message = "User not found." });

    return Results.Ok(new { success = true, message = "FCM token updated" });
});

// ─── Alerting & Notifications ──────────────────────────────────────

app.MapPost("/api/alerts", async ([FromBody] DisasterAlertRequest body, IHttpClientFactory httpClientFactory) =>
{
    var client = httpClientFactory.CreateClient();
    var response = await client.PostAsJsonAsync($"{pythonWorkerUrl}/process_intel", new { query = body.Query, mode = body.Mode });
    
    if (!response.IsSuccessStatusCode) return Results.StatusCode(502);
    
    var jsonOptions = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    var intelData = await response.Content.ReadFromJsonAsync<PythonIntelResponse>(jsonOptions);

    var primaryCluster = intelData?.ThreatClusters?.OrderByDescending(c => c.AggregatedSeverity).FirstOrDefault();
    var alertTitle = string.IsNullOrWhiteSpace(primaryCluster?.PrimaryThreat)
        ? (string.IsNullOrWhiteSpace(body.Query) ? "Disaster Alert" : body.Query.Trim())
        : primaryCluster!.PrimaryThreat!.Trim();
    var alertDescription = string.IsNullOrWhiteSpace(primaryCluster?.Summary)
        ? "Critical threat detected in your region."
        : primaryCluster!.Summary!.Trim();

    if (intelData?.ThreatClusters?.Any(c => c.AggregatedSeverity > 0.7) == true)
    {
        var dispatch = await BroadcastNotification(alertTitle, alertDescription, "disaster", textBeeApiKey, textBeeDeviceId);
        return Results.Ok(new
        {
            success = true,
            data = intelData,
            alert = new { title = alertTitle, description = alertDescription },
            dispatch
        });
    }

    return Results.Ok(new
    {
        success = true,
        data = intelData,
        alert = new { title = alertTitle, description = alertDescription }
    });
});

app.MapPost("/api/broadcast-alert", async ([FromBody] DisasterAlertRequest body) =>
{
    var title = string.IsNullOrWhiteSpace(body.Query) ? "Disaster Alert" : body.Query.Trim();
    var notificationBody = !string.IsNullOrWhiteSpace(body.Description)
        ? body.Description.Trim()
        : (string.IsNullOrWhiteSpace(body.Mode) ? "A new alert was issued." : body.Mode.Trim());

    var result = await BroadcastNotification(title, notificationBody, body.Mode ?? "alert", textBeeApiKey, textBeeDeviceId);
    return Results.Ok(new
    {
        success = true,
        alert = new { title, description = notificationBody },
        message = $"Sent notifications to {result.SmsRecipients} phone numbers and {result.FcmDelivered} push devices.",
        data = result
    });
});

app.MapGet("/api/test-notifications", async () =>
{
    const string title = "Test Alert";
    const string description = "This is a test notification from the backend.";
    var result = await BroadcastNotification(title, description, "test", textBeeApiKey, textBeeDeviceId);
    return Results.Ok(new
    {
        success = true,
        alert = new { title, description },
        message = $"Sent test notifications to {result.SmsRecipients} phone numbers and {result.FcmDelivered} push devices.",
        data = result
    });
});

// ─── SMS Endpoints ──────────────────────────────────────────────────

app.MapPost("/auth/send-code", async ([FromBody] SendCodeRequest body, IHttpClientFactory httpClientFactory, IConfiguration config) =>
{
    var phone = body.Phone.Trim();
    var code = GenerateCode();
    
    await SendSmsManual(phone, $"Hey, {code}", config, httpClientFactory);

    otpStore[phone] = new OtpEntry { Phone = phone, CodeHash = HashCode(code), ExpiresAtUtc = DateTime.UtcNow.AddMinutes(10) };
    return Results.Ok(new { success = true, message = "Code sent." });
});

app.MapPost("/auth/verify-code", ([FromBody] VerifyCodeRequest body) =>
{
    var phone = body.Phone?.Trim();
    var code = body.Code?.Trim();
    
    if (string.IsNullOrWhiteSpace(phone) || string.IsNullOrWhiteSpace(code))
        return Results.BadRequest(new { success = false, message = "Phone and code required." });

    if (!otpStore.TryGetValue(phone, out var entry))
        return Results.BadRequest(new { success = false, message = "No code sent to this phone." });

    if (DateTime.UtcNow > entry.ExpiresAtUtc)
        return Results.BadRequest(new { success = false, message = "Code expired." });

    if (HashCode(code) != entry.CodeHash)
        return Results.Json(new { success = false, message = "Invalid code." }, statusCode: 401);

    otpStore.TryRemove(phone, out _);
    return Results.Ok(new { success = true, message = "Code verified." });
});

app.MapGet("/db/rows", async (string table) => {
    if (!IsValidTable(table)) return Results.BadRequest();
    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();
    var cmd = conn.CreateCommand();
    cmd.CommandText = $"SELECT * FROM {table}";
    var rows = new List<object>();
    using var reader = await cmd.ExecuteReaderAsync();
    while (await reader.ReadAsync()) {
        var row = new Dictionary<string, object>();
        for (int i = 0; i < reader.FieldCount; i++) row[reader.GetName(i)] = reader.GetValue(i);
        rows.Add(row);
    }
    return Results.Ok(rows);
});

app.Run();

// ─── Helper Methods (Static) ────────────────────────────────────────

async Task<BroadcastDispatchResult> BroadcastNotification(string title, string body, string type, string? apiKey, string? deviceId)
{
    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();
    var cmd = conn.CreateCommand();
    cmd.CommandText = "SELECT id, fcm_token, phone FROM users WHERE phone IS NOT NULL AND TRIM(phone) != ''";
    using var reader = await cmd.ExecuteReaderAsync();

    var recipients = new List<(long UserId, string Phone, string? Token)>();
    while (await reader.ReadAsync())
    {
        var userId = reader.GetInt64(0);
        var phone = reader.GetString(2).Trim();
        var token = reader.IsDBNull(1) ? null : reader.GetString(1).Trim();

        if (!string.IsNullOrWhiteSpace(phone))
        {
            recipients.Add((userId, phone, string.IsNullOrWhiteSpace(token) ? null : token));
        }
    }

    var phoneNumbers = recipients
        .Select(r => r.Phone)
        .Where(phone => !string.IsNullOrWhiteSpace(phone))
        .ToHashSet(StringComparer.OrdinalIgnoreCase);

    var staleUserIds = new ConcurrentBag<long>();
    var pushTasks = recipients
        .Where(r => !string.IsNullOrWhiteSpace(r.Token))
        .Select(async recipient =>
        {
            var message = new Message()
            {
                Token = recipient.Token!,
                Notification = new Notification() { Title = title, Body = body },
                Data = new Dictionary<string, string>()
                {
                    { "type", type },
                    { "title", title },
                    { "body", body },
                    { "description", body }
                },
                Android = new AndroidConfig
                {
                    Priority = Priority.High
                }
            };

            try
            {
                await FirebaseMessaging.DefaultInstance.SendAsync(message);
                return true;
            }
            catch
            {
                staleUserIds.Add(recipient.UserId);
                return false;
            }
        })
        .ToArray();

    Task? smsTask = null;

    // Send SMS to all phone numbers
    if (phoneNumbers.Any() && !string.IsNullOrWhiteSpace(apiKey) && !string.IsNullOrWhiteSpace(deviceId))
    {
        var smsMessage = $"🚨 {title}\n{body}";
        var httpClient = new HttpClient();
        var payload = new { recipients = phoneNumbers.ToArray(), message = smsMessage };
        httpClient.DefaultRequestHeaders.Add("x-api-key", apiKey);
        smsTask = httpClient.PostAsJsonAsync($"https://api.textbee.dev/api/v1/gateway/devices/{deviceId}/send-sms", payload);
    }

    var allTasks = new List<Task>();
    allTasks.AddRange(pushTasks);
    if (smsTask != null)
    {
        allTasks.Add(smsTask);
    }

    if (allTasks.Count > 0)
    {
        try { await Task.WhenAll(allTasks); } catch { }
    }

    var deliveredCount = pushTasks.Count(task => task.Status == TaskStatus.RanToCompletion && task.Result);

    foreach (var userId in staleUserIds)
    {
        var cleanup = conn.CreateCommand();
        cleanup.CommandText = "UPDATE users SET fcm_token = NULL WHERE id = @id";
        cleanup.Parameters.AddWithValue("@id", userId);
        try { await cleanup.ExecuteNonQueryAsync(); } catch { }
    }

    return new BroadcastDispatchResult(deliveredCount, phoneNumbers.Count, staleUserIds.Count);
}

static bool TryGetFlexibleString(JsonElement body, out string? value, params string[] propertyNames)
{
    foreach (var propertyName in propertyNames)
    {
        if (!body.TryGetProperty(propertyName, out var property))
            continue;

        value = property.ValueKind switch
        {
            JsonValueKind.String => property.GetString(),
            JsonValueKind.Number or JsonValueKind.True or JsonValueKind.False => property.ToString(),
            _ => null
        };

        return true;
    }

    value = null;
    return false;
}

static async Task SendSmsManual(string phone, string message, IConfiguration config, IHttpClientFactory clientFactory)
{
    var apiKey = config["TEXTBEE_API_KEY"];
    var deviceId = config["TEXTBEE_DEVICE_ID"];
    var client = clientFactory.CreateClient();
    var payload = new { recipients = new[] { phone }, message = message };
    client.DefaultRequestHeaders.Add("x-api-key", apiKey);
    await client.PostAsJsonAsync($"https://api.textbee.dev/api/v1/gateway/devices/{deviceId}/send-sms", payload);
}

static string GenerateCode() => RandomNumberGenerator.GetInt32(100000, 999999).ToString();
static string HashCode(string code) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(code)));
static bool IsValidTable(string? name) => !string.IsNullOrWhiteSpace(name) && System.Text.RegularExpressions.Regex.IsMatch(name, @"^[a-zA-Z_][a-zA-Z0-9_]*$");

// ─── Type Definitions (MUST BE AT THE VERY BOTTOM) ──────────────────

public record SendCodeRequest(string Phone);
public record VerifyCodeRequest(string Phone, string Code);
public record DisasterAlertRequest(string Query, string Mode, string? Description, double? UserLat, double? UserLng);
public record BroadcastDispatchResult(int FcmDelivered, int SmsRecipients, int StaleTokens);
public class OtpEntry { public string Phone { get; set; } = ""; public string CodeHash { get; set; } = ""; public DateTime ExpiresAtUtc { get; set; } }
public class PythonIntelResponse { public List<ThreatCluster>? ThreatClusters { get; set; } }
public class ThreatCluster { public string? ClusterId { get; set; } public string? PrimaryThreat { get; set; } public double AggregatedSeverity { get; set; } public string? Summary { get; set; } public List<Coordinate>? DangerPolygon { get; set; } public int SourcePostCount { get; set; } }
public class Coordinate { public double Lat { get; set; } public double Lng { get; set; } }