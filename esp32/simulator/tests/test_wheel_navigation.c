/* Production LVGL UI, simulated camera/network services and wheel events. */
#define main simulator_cli_main
#include "../src/main.c"
#undef main
#include <assert.h>
#include "boards/watcher_camera.h"
#include "muse_wheel_gesture.h"
#include "muse_input.h"
#include "muse_voice.h"
#include "muse_wardrobe.h"
#include <unistd.h>
static pthread_mutex_t save_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t save_changed=PTHREAD_COND_INITIALIZER;
static bool save_blocked, save_entered;
/* Replace the simulator's weak in-memory persistence with a controllable
 * store. The real UI must stay responsive while this save is stalled. */
esp_err_t muse_wardrobe_settings_set(const char *id) {
    if(!muse_wardrobe_valid(id)) return ESP_ERR_INVALID_ARG;
    pthread_mutex_lock(&save_lock);
    save_entered=true;pthread_cond_broadcast(&save_changed);
    while(save_blocked) pthread_cond_wait(&save_changed,&save_lock);
    pthread_mutex_unlock(&save_lock);
    muse_wardrobe_select(id);return ESP_OK;
}
static void *deliver_weather(void *card) {
    assert(muse_ui_weather_show(card)==ESP_OK);return NULL;
}
static void step(unsigned ms) {
    for (unsigned t=0;t<ms;t+=5) { sim_time_advance_us(5000); lv_timer_handler(); }
}
static bool has_text(lv_obj_t *o,const char *text) {
    if(!lv_obj_is_visible(o)) return false;
    if(lv_obj_check_type(o,&lv_label_class) && strstr(lv_label_get_text(o),text)) return true;
    for(uint32_t i=0;i<lv_obj_get_child_count(o);i++) if(has_text(lv_obj_get_child(o,i),text)) return true;
    return false;
}
static void expect(const char *s) { if(!has_text(lv_screen_active(),s)) {fprintf(stderr,"Missing visible text: %s\n",s);abort();} }
static const char *output_dir;
static void snap(const char *name) {
    char p[512];snprintf(p,sizeof(p),"%s/%s.ppm",output_dir,name);assert(write_snapshot(p));
}
/* Optional evidence: real UI frames at 20 fps, with synthetic service state. */
static void clip(unsigned frames) {
    static unsigned frame;
    for(unsigned i=0;i<frames;i++) {
        char name[32];snprintf(name,sizeof(name),"cancel-frame-%03u",frame++);
        step(50);snap(name);
    }
}
static void turn(int d) {muse_ui_wheel_turn(d);step(180);}
static void click(void) {muse_ui_wheel_click(muse_voice_thinking_turn());step(180);}
static void card(uint16_t color,bool success) {
    uint16_t row[412];for(int x=0;x<412;x++) row[x]=(color>>8)|(color<<8);
    for(int y=0;y<412;y++) assert(muse_ui_card_draw(0,y,412,1,row));
    muse_ui_card_finish(success);step(200);
}
static const char *reply="Start here. The wheel lets you read this answer at your own pace. Turn forward to move to the next page and turn back to reread something. It will stay on your chosen page even after Muse finishes responding. Press the wheel when you are done reading. You can open the last reply again by turning the wheel while Muse is resting.";
int main(int argc, char **argv) {
    assert(argc == 2); output_dir = argv[1];
    setbuf(stdout,NULL);setenv("SDL_VIDEODRIVER","dummy",1);setenv("SDL_AUDIODRIVER","dummy",1);
    sim_time_reset();sim_services_reset();lv_init();muse_state_init();muse_state_set_power(&s_power);muse_board=sim_board_get();assert(muse_ui_start()==ESP_OK);
    muse_state_set_mode(MUSE_MODE_IDLE);muse_state_set_caption("%s", "");step(800);snap("01-idle");
    /* A touch-initiated send has not previously raised the wheel hint. */
    sim_services_set_camera("sending");muse_state_set_mode(MUSE_MODE_THINKING);step(200);
    int image_index=-1,hint_index=-1;
    for(uint32_t i=0;i<lv_obj_get_child_count(lv_screen_active());i++) {
        lv_obj_t *o=lv_obj_get_child(lv_screen_active(),i);
        if(!lv_obj_is_visible(o)) continue;
        if(lv_obj_check_type(o,&lv_image_class)) image_index=(int)i;
        if(lv_obj_check_type(o,&lv_label_class) && strstr(lv_label_get_text(o),"Press wheel to cancel")) hint_index=(int)i;
    }
    snap("18-photo-send-cancel-hint");
    assert(image_index>=0 && hint_index>image_index);
    click();assert(muse_state_mode(NULL)==MUSE_MODE_IDLE && watcher_camera_state()==WATCHER_CAMERA_CLOSED);
    muse_ui_reply_update("reply-1",reply);muse_state_set_mode(MUSE_MODE_SPEAKING);muse_state_set_caption("Start here.");step(200);
    turn(1);expect("Page 2/");snap("02-reading-page-2");
    muse_state_set_caption("Automatically advanced caption");step(500);expect("Page 2/");
    muse_state_set_mode(MUSE_MODE_IDLE);muse_state_set_caption("%s", "");step(500);expect("Page 2/");
    turn(-1);expect("Page 1/");expect("Start here.");snap("03-reading-page-1");
    click();assert(!has_text(lv_screen_active(),"Page 1/"));
    turn(1);expect("Last reply");snap("04-reply-browser");click();expect("Page 1/");click();
    puts("PASS manual forward/back pages, no automatic jump, survives reply completion, recalls last answer");
    card(0xf800,true);snap("05-saved-card");muse_ui_image_hide();step(200);
    sim_services_set_camera("live");step(200);card(0x07e0,false);snap("06-camera-with-failed-card");
    sim_services_set_camera("closed");step(200);turn(1);expect("Latest card");snap("07-card-browser");click();snap("08-recalled-card");
    puts("PASS card recall after camera and failed replacement (pixel comparison follows)");
    muse_ui_image_hide();sim_services_set_camera("review");step(200);
    turn(1);expect("Press wheel: Send photo");assert(watcher_camera_state()==WATCHER_CAMERA_REVIEW);snap("09-photo-send-selected");
    turn(1);expect("Press wheel: Retake");snap("10-photo-retake-selected");click();assert(watcher_camera_state()==WATCHER_CAMERA_LIVE);
    turn(1);expect("Press wheel: Take photo");click();assert(watcher_camera_state()==WATCHER_CAMERA_REVIEW);
    turn(-1);expect("Press wheel: Cancel");snap("11-photo-cancel-selected");click();assert(watcher_camera_state()==WATCHER_CAMERA_CLOSED);
    sim_services_set_camera("review");step(200);turn(1);click();assert(watcher_camera_state()==WATCHER_CAMERA_SENDING);
    puts("PASS photo rotation never sends; clicks confirm Retake, Take photo, Cancel, and simulated Send");
    sim_services_set_camera("review");step(200);turn(-1);expect("Press wheel: Cancel");
    muse_wheel_gesture_t gesture = {0};
    muse_wheel_step(&gesture,MUSE_WHEEL_DOWN,10,true);
    muse_wheel_step(&gesture,MUSE_WHEEL_UP,60,true);
    /* Input queues rotation, then passes the same edge to the recognizer. */
    muse_ui_wheel_turn(1);
    unsigned action=muse_wheel_step(&gesture,MUSE_WHEEL_TURN,160,true);
    if(action&MUSE_WHEEL_TAP) muse_ui_wheel_click(muse_voice_thinking_turn());
    step(200);expect("Press wheel: Send photo");
    action=muse_wheel_step(&gesture,0,420,true);
    if(action&MUSE_WHEEL_TAP) muse_ui_wheel_click(muse_voice_thinking_turn());
    step(200);assert(watcher_camera_state()==WATCHER_CAMERA_REVIEW);
    puts("PASS gesture-to-UI: click Cancel then rotate to Send does not upload");
    sim_services_set_camera("live");step(200);card(0x001f,true);assert(watcher_camera_state()==WATCHER_CAMERA_LIVE);snap("12-camera-during-new-card");
    sim_services_set_camera("closed");step(200);snap("13-deferred-card");
    muse_ui_image_hide();step(200);turn(1);step(10500);assert(!has_text(lv_screen_active(),"Press wheel to open"));
    puts("PASS completed card waits for camera close, browse hint times out");
    sim_services_set_camera("live");step(200);card(0x07e0,true);
    muse_ui_show_animation();sim_services_set_camera("closed");step(200);
    snap("14-cancelled-deferred-card");
    turn(1);expect("Latest card");click();snap("15-recall-cancelled-card");
    puts("PASS show_animation cancels deferred presentation but retains recall");
    muse_ui_image_hide();muse_state_set_mode(MUSE_MODE_THINKING);muse_state_set_caption("Waiting for Muse...");step(400);
    snap("16-thinking-before-cancel");
    if(getenv("MUSE_WHEEL_VIDEO")) {clip(40);muse_ui_wheel_click(muse_voice_thinking_turn());clip(60);} else click();
    assert(muse_state_mode(NULL)==MUSE_MODE_IDLE);
    assert(!has_text(lv_screen_active(),"Waiting for Muse"));snap("17-cancelled-idle");
    muse_state_set_mode(MUSE_MODE_THINKING);step(200);expect("Press wheel to cancel");
    sim_services_set_microphone(false);click();assert(muse_state_mode(NULL)==MUSE_MODE_IDLE);
    sim_services_set_microphone(true);
    muse_input_touch(MUSE_PTT_DOWN);assert(muse_state_mode(NULL)==MUSE_MODE_LISTENING);
    muse_input_touch(MUSE_PTT_UP);step(200);assert(muse_state_mode(NULL)==MUSE_MODE_THINKING);
    turn(1);expect("Page 2/");expect("Press wheel: cancel");click();assert(muse_state_mode(NULL)==MUSE_MODE_IDLE);
    assert(!has_text(lv_screen_active(),"Page 2/"));
    click();assert(muse_state_mode(NULL)==MUSE_MODE_IDLE);
    sim_services_set_camera("sending");muse_state_set_mode(MUSE_MODE_THINKING);step(200);
    expect("Press wheel to cancel");click();assert(muse_state_mode(NULL)==MUSE_MODE_IDLE);
    assert(watcher_camera_state()==WATCHER_CAMERA_CLOSED);
    muse_state_set_mode(MUSE_MODE_THINKING);
    muse_ui_wheel_click(muse_voice_thinking_turn());
    muse_input_touch(MUSE_PTT_DOWN);muse_input_touch(MUSE_PTT_UP);
    step(200);assert(muse_state_mode(NULL)==MUSE_MODE_THINKING);
    click();assert(muse_state_mode(NULL)==MUSE_MODE_IDLE);
    puts("PASS delayed navigation cancel cannot target the next turn");
    puts("PASS wheel cancels thinking and manual reading, works with mic off, permits next hold, preserves idle click");
    /* Native weather fixtures exercise the real UI and outfit renderer. */
    muse_state_set_caption("%s", "");step(5000);
    muse_weather_card_t weather = {.temp_f=75, .high_f=80, .low_f=57,
        .wind_mph=10, .humidity_pct=52, .present=15, .outfit_id="warm-crochet"};
    assert(muse_ui_weather_show(&weather)==ESP_OK);step(500);
    expect("75 F");expect("H 80   L 57");expect("WIND 10 MPH  RH 52%");
    assert(!strcmp(muse_wardrobe_current(),"warm-crochet"));snap("19-native-weather");
    step(550);snap("20-native-weather-moving");
    if(getenv("MUSE_WEATHER_VIDEO")) {
        for(unsigned i=0;i<100;i++) {
            if(i==30) muse_state_make_happy();
            char name[40];snprintf(name,sizeof(name),"weather-frame-%03u",i);
            step(50);snap(name);
        }
    }
    click();assert(!has_text(lv_screen_active(),"75 F"));
    turn(1);expect("Latest card");click();expect("75 F");
    card(0x07e0,false);expect("75 F");
    muse_ui_show_animation();step(200);assert(!has_text(lv_screen_active(),"75 F"));
    turn(1);click();expect("75 F");
    muse_input_touch(MUSE_PTT_DOWN);step(200);assert(!has_text(lv_screen_active(),"75 F"));
    muse_input_touch(MUSE_PTT_UP);step(200);
    weather.temp_f=-12;weather.present=MUSE_WEATHER_LOW;weather.low_f=-20;
    strcpy(weather.outfit_id,"freezing-puffer");
    assert(muse_ui_weather_show(&weather)==ESP_OK);step(200);
    assert(!has_text(lv_screen_active(),"-12 F"));
    click();muse_state_set_caption("%s", "");step(400);expect("-12 F");expect("L -20");
    assert(!has_text(lv_screen_active(),"WIND"));snap("21-native-weather-cold");
    sim_services_set_camera("live");step(200);
    weather.temp_f=100;weather.present=0;strcpy(weather.outfit_id,"hot-shorts");
    assert(muse_ui_weather_show(&weather)==ESP_OK);step(200);
    assert(!has_text(lv_screen_active(),"100 F"));
    muse_ui_show_animation();sim_services_set_camera("closed");step(200);
    assert(!has_text(lv_screen_active(),"100 F"));
    turn(1);click();expect("100 F");snap("22-native-weather-hot");
    muse_state_set_caption("A response takes priority");step(200);
    assert(!has_text(lv_screen_active(),"100 F"));
    muse_state_set_caption("%s", "");step(200);expect("100 F");
    card(0x001f,true);assert(!has_text(lv_screen_active(),"100 F"));
    muse_ui_show_animation();turn(1);click();assert(!has_text(lv_screen_active(),"100 F"));
    muse_ui_show_animation();
    lv_obj_t *tiles=NULL;
    for(uint32_t i=0;i<lv_obj_get_child_count(lv_screen_active());i++) {
        lv_obj_t *child=lv_obj_get_child(lv_screen_active(),i);
        if(lv_obj_check_type(child,&lv_tileview_class)) tiles=child;
    }
    assert(tiles);lv_tileview_set_tile_by_index(tiles,1,0,LV_ANIM_OFF);step(200);
    assert(muse_ui_weather_show(&weather)==ESP_OK);step(200);
    assert(!has_text(lv_screen_active(),"100 F"));
    lv_tileview_set_tile_by_index(tiles,0,0,LV_ANIM_OFF);step(600);expect("100 F");puts("PASS settings weather deferral");
    muse_ui_show_animation();sim_services_set_link_state(MUSE_LINK_CONFIRM);step(200);
    assert(muse_ui_weather_show(&weather)==ESP_OK);step(200);
    assert(!has_text(lv_screen_active(),"100 F"));
    sim_services_set_link_state(MUSE_LINK_ONLINE);step(600);expect("100 F");puts("PASS link weather deferral");
    muse_ui_show_animation();sim_services_set_paired(false);sim_services_set_link_state(MUSE_LINK_UNPAIRED);step(200);
    assert(muse_ui_weather_show(&weather)==ESP_OK);step(200);
    assert(!has_text(lv_screen_active(),"100 F"));
    sim_services_set_paired(true);sim_services_set_link_state(MUSE_LINK_ONLINE);step(600);expect("100 F");puts("PASS link weather deferral");
    puts("PASS native weather animations, recall, omitted values, camera/voice/caption priority and latest-card replacement");
    puts("PASS weather waits behind settings, pairing confirmation and initial setup");
    pthread_mutex_lock(&save_lock);save_blocked=true;save_entered=false;
    weather.temp_f=65;strcpy(weather.outfit_id,"mild-knit");
    pthread_t producer;assert(!pthread_create(&producer,NULL,deliver_weather,&weather));
    while(!save_entered) pthread_cond_wait(&save_changed,&save_lock);
    pthread_mutex_unlock(&save_lock);
    alarm(5); /* Fails promptly if a blocked save owns the UI's image mutex. */
    turn(1);expect("Latest card");
    alarm(0);
    pthread_mutex_lock(&save_lock);save_blocked=false;pthread_cond_broadcast(&save_changed);
    pthread_mutex_unlock(&save_lock);assert(!pthread_join(producer,NULL));
    click();expect("65 F");assert(!strcmp(muse_wardrobe_current(),"mild-knit"));
    puts("PASS wheel responds while outfit persistence is stalled; new forecast publishes afterward");
    lv_deinit();
    return 0;
}
