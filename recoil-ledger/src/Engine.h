#pragma once
#include <atomic>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#include "Model.h"

namespace rl {

enum class Mode : int { Idle = 0, Training = 1, Armed = 2 };

struct EngineSettings {
    std::atomic<float> strength{1.0f};      // 0..1.5
    std::atomic<float> humanize{0.35f};     // fraction of learned sigma
    std::atomic<float> currentSens{10.f};   // in-game sensitivity now
    std::atomic<bool> adsToggle{false};     // ADS bound as toggle instead of hold
    std::atomic<bool> requireAds{true};
    std::atomic<int> leanLeftVk{'Q'};
    std::atomic<int> leanRightVk{'E'};
    std::atomic<int> minTrainHoldMs{2000};
};

struct EngineStatus {
    std::atomic<int> mode{0};
    std::atomic<int> stance{0};
    std::atomic<bool> trigger{false};   // LMB held (and ADS if required)
    std::atomic<bool> ads{false};
    std::atomic<float> holdMs{0.f};     // current spray age
    std::atomic<uint32_t> shotPulse{0}; // bumps while spraying, drives UI kick
    std::atomic<uint32_t> committed{0};
    std::atomic<uint32_t> rejected{0};
};

class Engine {
public:
    Engine();
    ~Engine();

    void Start();
    void Stop();

    void SetMode(Mode m);
    Mode GetMode() const { return static_cast<Mode>(status.mode.load()); }
    void SetCompiled(std::shared_ptr<const Compiled> c);

    // Finished training sprays waiting for the UI thread.
    std::vector<RawSpray> DrainSprays();
    // Copy of the in-progress spray (cumulative), for the live plot.
    void LiveTrace(std::vector<float>& xs, std::vector<float>& ys, int& stance);

    EngineSettings settings;
    EngineStatus status;

private:
    void Run();
    void InputThread();
    Stance PollStance() const;

    std::thread thread_, input_;
    std::atomic<void*> inputHwnd_{nullptr};
    std::atomic<bool> running_{false};
    std::atomic<long long> rawAcc_[2]{};  // physical mouse counts since last engine tick
    std::atomic<std::shared_ptr<const Compiled>> compiled_;

    std::mutex outMu_;
    std::vector<RawSpray> finished_;
    std::vector<float> liveX_, liveY_;
    int liveStance_ = 0;
};

}  // namespace rl
