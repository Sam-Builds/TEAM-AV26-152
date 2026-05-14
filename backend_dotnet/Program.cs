using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();
builder.Services.AddCors(o =>
    o.AddDefaultPolicy(p => p.AllowAnyOrigin().AllowAnyMethod().AllowAnyHeader()));
builder.Services.AddHttpClient();

// ─── Configurations ────────────────────────────────────────────────────────
var textBeeBaseUrl = builder.Configuration["TEXTBEE_BASE_URL"] ?? "https://api.textbee.dev/api/v1";
var textBeeApiKey = builder.Configuration["TEXTBEE_API_KEY"];
var textBeeDeviceId = builder.Configuration["TEXTBEE_DEVICE_ID"];

// Single shared connection string
var connStr = "Data Source=app.db";

// ─── State ──────────────────────────────────────────────────────────────────
var otpStore = new ConcurrentDictionary<string, OtpEntry>();

// ─── Ensure tables exist on startup ──────────────────────────────────────────
using (var conn = new SqliteConnection(connStr))
{
    conn.Open();
    var cmd = conn.CreateCommand();
    cmd.CommandText = """
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    NOT NULL UNIQUE,
            email    TEXT    NOT NULL UNIQUE,
            phone        INTEGER NOT NULL,
            password TEXT    NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        ); 
         CREATE TABLE IF NOT EXISTS location (
            user_id     INTEGER PRIMARY KEY,
            home_name   TEXT,
            street_name TEXT,
            district_name       TEXT,
            state_name  TEXT,
            pin_code    INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
    """;
    cmd.ExecuteNonQuery();
}

var app = builder.Build();

app.UseSwagger();
app.UseSwaggerUI();
app.UseCors();

// ─── Helpers ─────────────────────────────────────────────────────────────────
static bool IsValidTable(string? name) =>
    !string.IsNullOrWhiteSpace(name) &&
    System.Text.RegularExpressions.Regex.IsMatch(name, @"^[a-zA-Z_][a-zA-Z0-9_]*$");

static bool IsPhoneE164(string phone) =>
    System.Text.RegularExpressions.Regex.IsMatch(phone, @"^\+[1-9][0-9]{7,14}$");

static string GenerateCode()
{
    var value = RandomNumberGenerator.GetInt32(100000, 999999);
    return value.ToString();
}

static string HashCode(string code)
{
    var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(code));
    return Convert.ToHexString(bytes);
}

// ─────────────────────────────────────────────────────────────────────────────
//  AUTH & OTP ENDPOINTS
// ─────────────────────────────────────────────────────────────────────────────

app.MapPost("/auth/register", async ([FromBody] Dictionary<string, string> body) =>
{
    if (!body.TryGetValue("username", out var username) || string.IsNullOrWhiteSpace(username))
        return Results.BadRequest(new { success = false, message = "username is required." });

    if (!body.TryGetValue("email", out var email) || string.IsNullOrWhiteSpace(email))
        return Results.BadRequest(new { success = false, message = "email is required." });
        
    if (!body.TryGetValue("phone", out var phone) || string.IsNullOrWhiteSpace(phone))
        return Results.BadRequest(new { success = false, message = "phone is required." });

    if (!body.TryGetValue("password", out var password) || string.IsNullOrWhiteSpace(password))
        return Results.BadRequest(new { success = false, message = "password is required." });

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var check = conn.CreateCommand();
    check.CommandText = "SELECT COUNT(*) FROM users WHERE email = $email OR username = $username";
    check.Parameters.AddWithValue("$email",    email.ToLower());
    check.Parameters.AddWithValue("$username", username.Trim());
    var count = (long)(await check.ExecuteScalarAsync())!;
    if (count > 0)
        return Results.Conflict(new { success = false, message = "Email or username already exists." });

    var insert = conn.CreateCommand();
    insert.CommandText = """
        INSERT INTO users (username, email, phone,password)
        VALUES ($username, $email, $phone, $password)
        RETURNING id, username, email, phone , created_at;
    """;
    insert.Parameters.AddWithValue("$username", username.Trim());
    insert.Parameters.AddWithValue("$email",    email.Trim().ToLower());
    insert.Parameters.AddWithValue("$phone",    phone);
    insert.Parameters.AddWithValue("$password", password);

    using var reader = await insert.ExecuteReaderAsync();
    await reader.ReadAsync();
    var user = new
    {
        id         = reader.GetInt64(0),
        username   = reader.GetString(1),
        email      = reader.GetString(2),
        created_at = reader.GetString(3)
    };

    return Results.Created($"/auth/users/{user.id}",
        new { success = true, message = "Registered successfully.", data = user });
})
.WithTags("Auth")
.WithSummary("Register a new user");

app.MapPost("/auth/login", async ([FromBody] Dictionary<string, string> body) =>
{
    if (!body.TryGetValue("email",    out var email)    || string.IsNullOrWhiteSpace(email))
        return Results.BadRequest(new { success = false, message = "email is required." });

    if (!body.TryGetValue("password", out var password) || string.IsNullOrWhiteSpace(password))
        return Results.BadRequest(new { success = false, message = "password is required." });

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var cmd = conn.CreateCommand();
    cmd.CommandText = "SELECT id, username, email, created_at FROM users WHERE email = $email AND password = $password";
    cmd.Parameters.AddWithValue("$email",    email.Trim().ToLower());
    cmd.Parameters.AddWithValue("$password", password);

    using var reader = await cmd.ExecuteReaderAsync();
    if (!await reader.ReadAsync())
        return Results.Unauthorized();

    var user = new
    {
        id         = reader.GetInt64(0),
        username   = reader.GetString(1),
        email      = reader.GetString(2),
        created_at = reader.GetString(3)
    };

    return Results.Ok(new { success = true, message = "Login successful.", data = user });
})
.WithTags("Auth")
.WithSummary("Login with email and password");

app.MapPost("/auth/send-code", async (
    [FromBody] SendCodeRequest body,
    IHttpClientFactory httpClientFactory) =>
{
    if (body is null || string.IsNullOrWhiteSpace(body.Phone))
        return Results.BadRequest(new { success = false, message = "phone is required." });

    var phone = body.Phone.Trim();
    if (!IsPhoneE164(phone))
        return Results.BadRequest(new { success = false, message = "phone must include country code, e.g. +2348012345678." });

    if (string.IsNullOrWhiteSpace(textBeeApiKey) || string.IsNullOrWhiteSpace(textBeeDeviceId))
        return Results.Problem(
            title: "SMS configuration missing",
            detail: "Set TEXTBEE_API_KEY and TEXTBEE_DEVICE_ID environment variables.",
            statusCode: 500);

    var code = GenerateCode();
    var message = $"Hey bro, just testing my api {code}";

    var payload = new TextBeeSmsRequest(
        Recipients: new[] { phone },
        Message: message,
        Sim: 1);
        
    var client = httpClientFactory.CreateClient();
    client.DefaultRequestHeaders.Add("x-api-key", textBeeApiKey);

    var requestUrl = $"{textBeeBaseUrl.TrimEnd('/')}/gateway/devices/{textBeeDeviceId}/send-sms";
    using var response = await client.PostAsJsonAsync(requestUrl, payload);

    if (!response.IsSuccessStatusCode)
    {
        var providerBody = await response.Content.ReadAsStringAsync();
        return Results.Json(new
        {
            success = false,
            message = "Failed to send SMS via provider.",
            provider_status = (int)response.StatusCode,
            provider_response = providerBody
        }, statusCode: 502);
    }

    otpStore[phone] = new OtpEntry
    {
        Phone = phone,
        CodeHash = HashCode(code),
        ExpiresAtUtc = DateTime.UtcNow.AddMinutes(10),
        Attempts = 0
    };

    return Results.Ok(new { success = true, message = "Verification code sent." });
})
.WithTags("Auth")
.WithSummary("Send SMS verification code");

app.MapPost("/auth/verify-code", ([FromBody] VerifyCodeRequest body) =>
{
    if (body is null || string.IsNullOrWhiteSpace(body.Phone) || string.IsNullOrWhiteSpace(body.Code))
        return Results.BadRequest(new { success = false, message = "phone and code are required." });

    var phone = body.Phone.Trim();
    var code = body.Code.Trim();

    if (!otpStore.TryGetValue(phone, out var otp))
        return Results.NotFound(new { success = false, message = "No code found for this phone. Send code first." });

    if (DateTime.UtcNow > otp.ExpiresAtUtc)
    {
        otpStore.TryRemove(phone, out _);
        return Results.BadRequest(new { success = false, message = "Code expired. Please resend." });
    }

    if (otp.Attempts >= 5)
        return Results.BadRequest(new { success = false, message = "Too many attempts. Please resend code." });

    otp.Attempts += 1;
    if (!string.Equals(otp.CodeHash, HashCode(code), StringComparison.Ordinal))
        return Results.BadRequest(new { success = false, message = "Invalid verification code." });

    otpStore.TryRemove(phone, out _);
    return Results.Ok(new { success = true, message = "Phone verified." });
})
.WithTags("Auth")
.WithSummary("Verify SMS code");

app.MapGet("/api/test", () => "API is working!.");

// ─────────────────────────────────────────────────────────────────────────────
//  DATABASE CRUD ENDPOINTS
// ─────────────────────────────────────────────────────────────────────────────

app.MapGet("/db/rows", async (
    string?  table,
    string?  where,
    string?  value,
    int?     limit,
    int?     offset) =>
{
    if (!IsValidTable(table))
        return Results.BadRequest(new { success = false, message = "Provide a valid ?table= name." });

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var sql = $"SELECT * FROM \"{table}\"";
    var cmd = conn.CreateCommand();

    if (!string.IsNullOrWhiteSpace(where) && value is not null)
    {
        if (!IsValidTable(where))
            return Results.BadRequest(new { success = false, message = "Invalid column name." });
        sql += $" WHERE \"{where}\" = $val";
        cmd.Parameters.AddWithValue("$val", value);
    }

    sql += $" LIMIT {Math.Clamp(limit ?? 100, 1, 1000)} OFFSET {Math.Max(offset ?? 0, 0)}";
    cmd.CommandText = sql;

    var rows = new List<Dictionary<string, object?>>();
    using var reader = await cmd.ExecuteReaderAsync();
    while (await reader.ReadAsync())
    {
        var row = new Dictionary<string, object?>();
        for (int i = 0; i < reader.FieldCount; i++)
            row[reader.GetName(i)] = reader.IsDBNull(i) ? null : reader.GetValue(i);
        rows.Add(row);
    }

    return Results.Ok(new { success = true, count = rows.Count, data = rows });
})
.WithTags("Database")
.WithSummary("SELECT rows from any table");

app.MapPost("/db/insert", async (string? table, [FromBody] Dictionary<string, object?> body) =>
{
    if (!IsValidTable(table))
        return Results.BadRequest(new { success = false, message = "Provide a valid ?table= name." });

    if (body is null || body.Count == 0)
        return Results.BadRequest(new { success = false, message = "Request body cannot be empty." });

    var columns = body.Keys.ToList();
    var paramNames = columns.Select((c, i) => $"$p{i}").ToList();

    var sql = $"""
        INSERT INTO "{table}" ({string.Join(", ", columns.Select(c => $"\"{c}\""))})
        VALUES ({string.Join(", ", paramNames)})
        RETURNING *;
    """;

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var cmd = conn.CreateCommand();
    cmd.CommandText = sql;
    for (int i = 0; i < columns.Count; i++)
        cmd.Parameters.AddWithValue(paramNames[i], body[columns[i]] ?? DBNull.Value);

    var inserted = new Dictionary<string, object?>();
    using var reader = await cmd.ExecuteReaderAsync();
    if (await reader.ReadAsync())
        for (int i = 0; i < reader.FieldCount; i++)
            inserted[reader.GetName(i)] = reader.IsDBNull(i) ? null : reader.GetValue(i);

    return Results.Created($"/db/rows?table={table}",
        new { success = true, message = "Row inserted.", data = inserted });
})
.WithTags("Database")
.WithSummary("INSERT a row into any table");

app.MapPut("/db/update", async (
    string?  table,
    string?  id,
    string?  idColumn,
    [FromBody] Dictionary<string, object?> body) =>
{
    if (!IsValidTable(table))
        return Results.BadRequest(new { success = false, message = "Provide a valid ?table= name." });

    if (string.IsNullOrWhiteSpace(id))
        return Results.BadRequest(new { success = false, message = "Provide ?id= of the row to update." });

    if (body is null || body.Count == 0)
        return Results.BadRequest(new { success = false, message = "Request body cannot be empty." });

    var keyCol = string.IsNullOrWhiteSpace(idColumn) ? "id" : idColumn;
    if (!IsValidTable(keyCol))
        return Results.BadRequest(new { success = false, message = "Invalid idColumn name." });

    var columns = body.Keys.ToList();
    var setClauses = columns.Select((c, i) => $"\"{c}\" = $p{i}").ToList();

    var sql = $"""
        UPDATE "{table}"
        SET {string.Join(", ", setClauses)}
        WHERE "{keyCol}" = $id
        RETURNING *;
    """;

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var cmd = conn.CreateCommand();
    cmd.CommandText = sql;
    for (int i = 0; i < columns.Count; i++)
        cmd.Parameters.AddWithValue($"$p{i}", body[columns[i]] ?? DBNull.Value);
    cmd.Parameters.AddWithValue("$id", id);

    var updated = new Dictionary<string, object?>();
    using var reader = await cmd.ExecuteReaderAsync();
    if (!await reader.ReadAsync())
        return Results.NotFound(new { success = false, message = $"No row with {keyCol} = {id} found in {table}." });

    for (int i = 0; i < reader.FieldCount; i++)
        updated[reader.GetName(i)] = reader.IsDBNull(i) ? null : reader.GetValue(i);

    return Results.Ok(new { success = true, message = "Row updated.", data = updated });
})
.WithTags("Database")
.WithSummary("UPDATE a row in any table by ID");

app.MapDelete("/db/delete", async (string? table, string? id, string? idColumn) =>
{
    if (!IsValidTable(table))
        return Results.BadRequest(new { success = false, message = "Provide a valid ?table= name." });

    if (string.IsNullOrWhiteSpace(id))
        return Results.BadRequest(new { success = false, message = "Provide ?id= of the row to delete." });

    var keyCol = string.IsNullOrWhiteSpace(idColumn) ? "id" : idColumn;
    if (!IsValidTable(keyCol))
        return Results.BadRequest(new { success = false, message = "Invalid idColumn name." });

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var cmd = conn.CreateCommand();
    cmd.CommandText = $"DELETE FROM \"{table}\" WHERE \"{keyCol}\" = $id";
    cmd.Parameters.AddWithValue("$id", id);

    var affected = await cmd.ExecuteNonQueryAsync();

    return affected == 0
        ? Results.NotFound(new { success = false, message = $"No row with {keyCol} = {id} found in {table}." })
        : Results.Ok(new { success = true, message = $"{affected} row(s) deleted." });
})
.WithTags("Database")
.WithSummary("DELETE a row from any table by ID");

app.MapGet("/db/tables", async () =>
{
    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var cmd = conn.CreateCommand();
    cmd.CommandText = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name";

    var tables = new List<string>();
    using var reader = await cmd.ExecuteReaderAsync();
    while (await reader.ReadAsync())
        tables.Add(reader.GetString(0));

    return Results.Ok(new { success = true, data = tables });
})
.WithTags("Database")
.WithSummary("List all tables in the SQLite database");

app.MapGet("/db/schema", async (string? table) =>
{
    if (!IsValidTable(table))
        return Results.BadRequest(new { success = false, message = "Provide a valid ?table= name." });

    using var conn = new SqliteConnection(connStr);
    await conn.OpenAsync();

    var cmd = conn.CreateCommand();
    cmd.CommandText = $"PRAGMA table_info(\"{table}\")";

    var columns = new List<object>();
    using var reader = await cmd.ExecuteReaderAsync();
    while (await reader.ReadAsync())
        columns.Add(new
        {
            cid        = reader.GetInt32(0),
            name       = reader.GetString(1),
            type       = reader.GetString(2),
            not_null   = reader.GetBoolean(3),
            pk         = reader.GetInt32(5) > 0
        });

    return columns.Count == 0
        ? Results.NotFound(new { success = false, message = $"Table '{table}' not found." })
        : Results.Ok(new { success = true, table, data = columns });
})
.WithTags("Database")
.WithSummary("Show column schema for a table");

app.Run();

// ─── Models ──────────────────────────────────────────────────────────────────
sealed class SendCodeRequest
{
    public string Phone { get; set; } = string.Empty;
}

sealed class VerifyCodeRequest
{
    public string Phone { get; set; } = string.Empty;
    public string Code { get; set; } = string.Empty;
}

sealed class OtpEntry
{
    public string Phone { get; set; } = string.Empty;
    public string CodeHash { get; set; } = string.Empty;
    public DateTime ExpiresAtUtc { get; set; }
    public int Attempts { get; set; }
}

sealed record TextBeeSmsRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("recipients")] string[] Recipients,
    [property: System.Text.Json.Serialization.JsonPropertyName("message")] string Message,
    [property: System.Text.Json.Serialization.JsonPropertyName("sim")] int? Sim = null);
    
