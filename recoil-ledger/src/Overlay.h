#pragma once
#include <string>

namespace rl {

// Click-through topmost toast, top-left. Slides in with overshoot, drains a life bar, slides out.
// Visible over borderless-windowed games.
class Overlay {
public:
    bool Create(void* hInstance);
    void Destroy();
    void Show(const std::string& line1, const std::string& line2, unsigned accentRgb);
    void Tick();  // call every frame
private:
    enum class Phase { Hidden, In, Hold, Out };
    void Paint(float slide, float alpha, float life, float pulse);
    void* hwnd_ = nullptr;
    std::string l1_, l2_;
    unsigned accent_ = 0;
    Phase phase_ = Phase::Hidden;
    double phaseStart_ = 0, pulseAt_ = -1e9;
};

}  // namespace rl
