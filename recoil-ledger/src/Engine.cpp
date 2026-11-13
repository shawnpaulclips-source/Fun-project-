#include "Engine.h"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <timeapi.h>

#include <array>
#include <cmath>
#include <random>

namespace rl {
namespace {

bool Down(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }

void SendMove(int dx, int dy) {
    if (!dx && !dy) return;
    INPUT in{};
    in.type = INPUT_MOUSE;
    in.mi.dx = dx;
    in.mi.dy = dy;
    in.mi.dwFlags = MOUSEEVENTF_MOVE;
    SendInput(1, &in, sizeof(in));
}

// Low-passed unit-variance Gaussian noise: smooth wander rather than jitter.
struct Wander {
    std::mt19937 rng{std::random_device{}()};
    std::normal_distribution<float> n{0.f, 1.f};
    float a = 0.015f, v = 0.f, gain = std::sqrt((2.f - 0.015f) / 0.015f);
    float Next() { v += a * (n(rng) - v); return v * gain; }
    void Reset() { v = 0.f; }
};

}  // namespace

Engine::Engine() = default;
Engine::~Engine() { Stop(); }

void Engine::Start() {
    if (running_.exchange(true)) return;
    thread_ = std::thread([this] { Run(); });
}

void Engine::Stop() {
    if (!running_.exchange(false)) return;
    if (thread_.joinable()) thread_.join();
}

void Engine::FeedRaw(long dx, long dy) {
    rawX_.fetch_add(dx, std::memory_order_relaxed);
    rawY_.fetch_add(dy, std::memory_order_relaxed);
}

void Engine::SetMode(Mode m) { status.mode.store(static_cast<int>(m)); }

void Engine::SetCompiled(std::shared_ptr<const Compiled> c) { compiled_.store(std::move(c)); }

std::vector<RawSpray> Engine::DrainSprays() {
    std::lock_guard lk(outMu_);
    std::vector<RawSpray> out;
    out.swap(finished_);
    return out;
}

void Engine::LiveTrace(std::vector<float>& xs, std::vector<float>& ys, int& stance) {
    std::lock_guard lk(outMu_);
    xs = liveX_;
    ys = liveY_;
    stance = liveStance_;
}

Stance Engine::PollStance() const {
    const bool l = Down(settings.leanLeftVk.load());
    const bool r = Down(settings.leanRightVk.load());
    if (l && !r) return Stance::LeanLeft;
    if (r && !l) return Stance::LeanRight;
    return Stance::Center;  // neither, or both (Siege resolves both to center)
}

void Engine::Run() {
    timeBeginPeriod(1);
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);
    HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (!timer) timer = CreateWaitableTimerW(nullptr, FALSE, nullptr);

    LARGE_INTEGER freq, now;
    QueryPerformanceFrequency(&freq);
    auto ms = [&] { QueryPerformanceCounter(&now); return now.QuadPart * 1000.0 / freq.QuadPart; };

    bool prevF6 = false, prevF7 = false, prevF8 = false, prevRmb = false, adsLatched = false;
    bool spraying = false;
    double sprayStart = 0, lastT = 0;
    RawSpray spray;
    std::array<int, kStanceCount> stanceTicks{};
    float accX = 0, accY = 0;          // sub-pixel carry
    float emittedX = 0, emittedY = 0;  // cumulative target already sent
    Wander wx, wy;
    float cumX = 0, cumY = 0;
    float offX = 0, offY = 0;

    while (running_.load()) {
        LARGE_INTEGER due;
        due.QuadPart = -10000;  // 1 ms
        SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE);
        WaitForSingleObject(timer, 2);

        const double t = ms();
        const long long rdx = rawX_.exchange(0, std::memory_order_relaxed);
        const long long rdy = rawY_.exchange(0, std::memory_order_relaxed);

        // Hotkeys: F6 arm, F7 train, F8 kill.
        const bool f6 = Down(VK_F6), f7 = Down(VK_F7), f8 = Down(VK_F8);
        Mode mode = GetMode();
        if (f6 && !prevF6) mode = mode == Mode::Armed ? Mode::Idle : Mode::Armed;
        if (f7 && !prevF7) mode = mode == Mode::Training ? Mode::Idle : Mode::Training;
        if (f8 && !prevF8) mode = Mode::Idle;
        prevF6 = f6; prevF7 = f7; prevF8 = f8;
        SetMode(mode);

        const bool rmb = Down(VK_RBUTTON);
        if (settings.adsToggle.load()) {
            if (rmb && !prevRmb) adsLatched = !adsLatched;
        } else {
            adsLatched = rmb;
        }
        prevRmb = rmb;
        const bool lmb = Down(VK_LBUTTON);
        if (!lmb && settings.adsToggle.load() && mode == Mode::Idle) adsLatched = false;
        const bool ads = adsLatched;
        const bool trigger = lmb && (ads || !settings.requireAds.load());
        const Stance stance = PollStance();

        status.ads.store(ads);
        status.trigger.store(trigger);
        status.stance.store(static_cast<int>(stance));

        if (trigger && !spraying && mode != Mode::Idle) {
            spraying = true;
            sprayStart = lastT = t;
            spray = RawSpray{};
            if (auto c = compiled_.load()) spray.loadoutKey = c->key;
            stanceTicks.fill(0);
            accX = accY = emittedX = emittedY = 0;
            cumX = cumY = 0;
            offX = offY = 0;
            wx.Reset();
            wy.Reset();
            std::lock_guard lk(outMu_);
            liveX_.clear();
            liveY_.clear();
        }

        if (spraying && (!trigger || mode == Mode::Idle)) {
            spraying = false;
            status.holdMs.store(0.f);
            if (mode == Mode::Training || GetMode() == Mode::Training) {
                int best = 0;
                for (int i = 1; i < kStanceCount; ++i)
                    if (stanceTicks[i] > stanceTicks[best]) best = i;
                const int total = stanceTicks[0] + stanceTicks[1] + stanceTicks[2];
                const bool longEnough = spray.DurationMs() >= settings.minTrainHoldMs.load();
                const bool cleanStance = total > 0 && stanceTicks[best] >= total * 0.85;
                if (longEnough && cleanStance && !spray.loadoutKey.empty()) {
                    spray.stance = static_cast<Stance>(best);
                    std::lock_guard lk(outMu_);
                    finished_.push_back(std::move(spray));
                    status.committed.fetch_add(1);
                } else {
                    status.rejected.fetch_add(1);
                }
            }
            continue;
        }

        if (!spraying) continue;

        const double age = t - sprayStart;
        status.holdMs.store(static_cast<float>(age));
        status.shotPulse.fetch_add(1, std::memory_order_relaxed);

        if (mode == Mode::Training) {
            // Raw counts come only from physical devices, so nothing we inject ever lands here.
            spray.ticks.push_back({age, static_cast<float>(rdx), static_cast<float>(rdy)});
            stanceTicks[static_cast<int>(stance)]++;
            cumX += rdx;
            cumY += rdy;
            std::lock_guard lk(outMu_);
            liveX_.push_back(cumX);
            liveY_.push_back(cumY);
            liveStance_ = static_cast<int>(stance);
        } else if (mode == Mode::Armed) {
            auto c = compiled_.load();
            const Learned* L = c ? c->For(stance) : nullptr;
            if (!L || age > L->LengthMs() + kGridMs) { lastT = t; continue; }

            const float scale = settings.strength.load() *
                                (c->sensAtCapture / std::max(0.1f, settings.currentSens.load()));
            const float hum = settings.humanize.load();

            // Target is the stance's own cumulative curve at this instant. Switching
            // stance mid-spray just changes which curve the next delta is read from,
            // so the handoff is instant with no snap.
            float px, py, cx, cy, sx, sy;
            L->At(lastT - sprayStart, px, py);
            L->At(age, cx, cy);
            L->SigmaAt(age, sx, sy);
            const float nx = wx.Next() * sx * hum, ny = wy.Next() * sy * hum;
            // Noise is a bounded offset around the curve (sized by his own spread), not accumulated drift.
            const float dx = (cx - px) * scale + (nx - offX);
            const float dy = (cy - py) * scale + (ny - offY);
            offX = nx;
            offY = ny;
            emittedX += dx;
            emittedY += dy;
            accX += dx;
            accY += dy;
            const int ix = static_cast<int>(std::trunc(accX));
            const int iy = static_cast<int>(std::trunc(accY));
            accX -= ix;
            accY -= iy;
            SendMove(ix, iy);

            std::lock_guard lk(outMu_);
            liveX_.push_back(emittedX);
            liveY_.push_back(emittedY);
            liveStance_ = static_cast<int>(stance);
        }
        lastT = t;
    }

    if (timer) CloseHandle(timer);
    timeEndPeriod(1);
}

}  // namespace rl
