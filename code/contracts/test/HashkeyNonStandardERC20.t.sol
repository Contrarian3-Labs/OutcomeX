// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT} from "../src/MachineAssetNFT.sol";
import {MachineMarketplace} from "../src/MachineMarketplace.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {OrderPaymentRouter} from "../src/OrderPaymentRouter.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {MockUSDCWithAuthorization} from "../src/mocks/MockUSDCWithAuthorization.sol";
import {MockNoReturnERC20} from "../src/mocks/MockNoReturnERC20.sol";
import {OrderRecord, OrderStatus} from "../src/types/OutcomeXTypes.sol";
import {TestBase} from "./utils/TestBase.sol";

contract HashkeyNonStandardERC20Test is TestBase {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant PLATFORM_TREASURY = address(0xBEEF);
    address internal constant MACHINE_OWNER = address(0xCAFE);
    address internal constant BUYER = address(0xB0B);

    MockUSDCWithAuthorization internal usdc;
    MockNoReturnERC20 internal usdt;
    PWRToken internal pwr;
    MachineAssetNFT internal machineAsset;
    RevenueVault internal revenueVault;
    SettlementController internal settlement;
    OrderBook internal orderBook;
    OrderPaymentRouter internal router;
    MachineMarketplace internal marketplace;

    uint256 internal machineId;

    function _stablecoinUnitsForCents(uint256 amountCents) internal pure returns (uint256) {
        return amountCents * 10_000;
    }

    function setUp() public {
        usdc = new MockUSDCWithAuthorization();
        usdt = new MockNoReturnERC20("HashKey USDT", "USDT", 6);
        pwr = new PWRToken(ADMIN);
        machineAsset = new MachineAssetNFT(ADMIN);
        revenueVault = new RevenueVault(ADMIN, address(pwr), address(machineAsset), 25);
        settlement = new SettlementController(ADMIN, address(revenueVault), address(pwr), PLATFORM_TREASURY);
        orderBook = new OrderBook(ADMIN, address(machineAsset));
        router = new OrderPaymentRouter(ADMIN, address(orderBook), address(usdc), address(usdt), address(pwr));

        address[] memory supportedTokens = new address[](1);
        supportedTokens[0] = address(usdt);
        marketplace = new MachineMarketplace(ADMIN, address(machineAsset), supportedTokens);

        vm.startPrank(ADMIN);
        pwr.setMinter(address(revenueVault), true);
        revenueVault.setSettlementController(address(settlement));
        router.setSettlementEscrow(address(settlement));
        settlement.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlement));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(address(router));
        machineAsset.setTransferGuard(address(orderBook));
        machineId = machineAsset.mintMachine(MACHINE_OWNER, "ipfs://machine-001");
        usdt.mint(ADMIN, 5_000_000);
        usdt.mint(BUYER, 5_000_000);
        vm.stopPrank();
    }

    function testAdapterPaymentSupportsNoReturnUSDT() public {
        vm.prank(ADMIN);
        uint256 orderId = router.createOrderByAdapter(BUYER, machineId, 100);

        vm.startPrank(ADMIN);
        usdt.approve(address(router), _stablecoinUnitsForCents(100));
        router.payOrderByAdapter(orderId, _stablecoinUnitsForCents(100), address(usdt));
        vm.stopPrank();

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(usdt.balanceOf(address(settlement)), _stablecoinUnitsForCents(100), "settlement should escrow usdt");
    }

    function testSettlementClaimSupportsNoReturnUSDT() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 100);

        vm.prank(BUYER);
        usdt.approve(address(router), _stablecoinUnitsForCents(100));

        vm.prank(BUYER);
        router.payWithUSDT(orderId, _stablecoinUnitsForCents(100), 0, 0, "");

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        vm.prank(PLATFORM_TREASURY);
        uint256 claimed = settlement.claimPlatformRevenue(address(usdt));
        assertEq(claimed, _stablecoinUnitsForCents(10), "platform claim mismatch");
        assertEq(usdt.balanceOf(PLATFORM_TREASURY), _stablecoinUnitsForCents(10), "treasury should receive usdt");
    }

    function testMarketplaceSupportsNoReturnUSDT() public {
        vm.prank(MACHINE_OWNER);
        machineAsset.approve(address(marketplace), machineId);

        vm.prank(MACHINE_OWNER);
        uint256 listingId =
            marketplace.createListing(machineId, address(usdt), 1_250_000, uint64(block.timestamp + 30 days));

        vm.prank(BUYER);
        usdt.approve(address(marketplace), 1_250_000);

        vm.prank(BUYER);
        marketplace.buyListing(listingId);

        assertEq(machineAsset.ownerOf(machineId), BUYER, "buyer should receive NFT");
        assertEq(usdt.balanceOf(MACHINE_OWNER), 1_250_000, "seller should receive usdt");
    }
}
